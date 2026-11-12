# HAMCP-371 S4: image identity, provenance and catalog capture

Written 2026-11-11 (Central). This directory holds what was established before
the ha-mcp 9.2.0 image was pulled, and the receipts of the local capture lane
(`scripts/upstream_catalog_capture_local.py`). Nothing here admits a release,
binds a provider or changes a reviewed value.

## Before the pull

| Item | Value |
|---|---|
| Image | `ghcr.io/homeassistant-ai/ha-mcp:9.2.0` |
| Index digest | `sha256:9de5bf6c4216142fdffe862900abe72dac8dfe9621dd0b2b050d132372b676ba` |
| linux/amd64 | `sha256:349a3ec6b2f4fa0797651b2c5d8985ca28d7a1028156eefba8d94fe51683fdb8` |
| linux/arm64 | `sha256:5799ff6f0b85f01710b725792e32caf79255934a6e80b8af28373768d42e9a80` |
| Resolved | 2026-11-11 22:32 CST, anonymous registry read (empty Docker configuration directory) |
| Source commit checked | `51e3aac788482a081807591cd5d58d07dc4e540b` (tag `v9.2.0`) |
| Verifier | `scripts/verify_upstream_image_provenance.py`, exit code 0 |
| Result | `slsa_provenance: present_per_platform`, `statement_signature: unsigned_buildkit_in_toto`, `sbom: absent`, `materials_declared_complete: false`, `label_evidence: informational_only` |
| Workflow | `homeassistant-ai/ha-mcp/.github/workflows/release-publish.yml@refs/heads/master` |
| Builder | `https://github.com/homeassistant-ai/ha-mcp/actions/runs/43058645294/attempts/1` |
| Attested revision | `4d7148309af7e4d3da95e16ced8bb67d6a43b9c1` |
| Host before the pull | root file system 144 GB, 51 GB free; about 12 GB memory available; Docker images 9.35 GB |

The tag and the digest were inspected separately and agree
(`inspect-tag-9.2.0.txt`, `inspect-digest-9.2.0.txt`). The SHA-256 of
`index-9.2.0.raw.json` is the index digest.

### Attested revision and source commit

The attestation names revision `4d714830...`, not the tag commit `51e3aac7...`.
`attested-revision-vs-source.txt` records the comparison, made with git
plumbing on the existing local clone:

- the source commit is an ancestor of the attested revision;
- two commits lie between them, both by `github-actions[bot]`: a changelog
  sync and "publish version 9.2.0";
- the only paths that differ are `homeassistant-addon/CHANGELOG.md` and
  `homeassistant-addon/config.yaml`. No path under `src/` differs.

So the image was built from the tagged source plus the upstream add-on's own
release bookkeeping. What the provenance does not establish: the statement is
unsigned, there is no SBOM and the materials list is not declared complete.
Those are properties of the upstream build and are the same kind the reviewed
9.1.2 row was admitted with; they are recorded, not waived, here.

`provenance-9.2.0.json` is the verifier's output, unedited. The capture lane
takes the 9.2.0 digest and expected labels from that file and refuses if it is
missing or names another source commit.

## Capture lane

`scripts/upstream_catalog_capture_local.py` follows the reviewed
`prepare-ha-mcp-release-registry-update.yml` capture (same fixture, same peer
environment, same capture command run twice and compared) with a stricter
network posture:

- one network namespace whose only interface is loopback. The fixture
  container is started with `--network none`; the ha-mcp container joins that
  namespace; every client runs inside it. No Docker network is created, no
  port is published, and there is no route out;
- the posture is verified on the running containers before the upstream image
  is started and before any request is sent, from container inspection and
  from a probe inside the namespace (interfaces, routes, and a refused
  connection to a documentation address). A mismatch stops the run;
- images are used only by digest from the local cache with `--pull never`;
  each container has an explicit memory limit, a read-only root file system,
  all capabilities dropped and no new privileges;
- the redaction setting is the image default (nothing is set), recorded in
  every receipt;
- the skills and resource file list is read from a container that is created
  and exported, never started. Nothing from the image is written to disk
  except the list of paths, sizes and SHA-256 values.

**The Home Assistant side of this lane is the repository's synthetic read-only
fixture (`scripts/fake_ha_read_gateway_contract_server.py`), not Home Assistant
Core.** No Core was started in S4. The `initialize` result and the tool
catalog do not depend on Home Assistant data; observations against a disposable
Core are a later step (S6).

The helper image is this repository's published Engineering image, used only as
a Python runtime with the pinned MCP SDK. The code it runs is this checkout's
`scripts/` and `hass_mcp_engineering_beta/`, mounted read-only.

Tests: `tests/test_upstream_catalog_capture_local.py` (offline; Docker is
replaced by a scripted stand-in).

## Launches

Every launch went through the host heavy-job gate (`receipts/gate-*.txt` are
the gate's own admission records; `receipts/launch-log.jsonl` is the lane's
log, one line per launch, nothing removed). Times are Central, 2026-11-11.

| # | Launch | Harness commit | Result | Notes |
|---|---|---|---|---|
| 1 | `selftest` (helper image only) | `a6b96736` | WITHHELD after start, `fixture_recorded_a_mutation_surface` | 22:55. Harness defect, see below. Cleanup clean. No upstream image had been started. |
| 2 | `selftest` | `fd68e780` | PASS | 22:56. Launched after a tracked harness fix; the note in its receipt records refusal, cause and fix. |
| 3 | `pull --version 9.2.0` | `fd68e780` | PASS | 22:56. By the bound index digest, anonymous, after the provenance record above was committed. Root file system free space 50.2 GB before, 50.0 GB after. |
| 4 | `run --version 9.1.2` | `fd68e780` | PASS | 22:56 to 22:57, 15.5 s. Control run on the admitted image. |
| 5 | `run --version 9.2.0` | `fd68e780` | PASS | 23:01, 14.8 s. |

Launch 1 is a failure after a run had started, so it is reported and was not
repeated for a pass on the same code. Its cause was in the harness, not in the
environment: the fixture reports its mutation counters as maps and lists, and
the lane compared each with the number zero, so an empty map was read as a
recorded mutation. The fix (`fd68e780`) is a tracked change with tests that use
the fixture's real shape. It is recorded here as a harness change. Launch 2 is
a new launch of the corrected harness, not an environmental relaunch.

While a launch runs, the gate counts it twice (the systemd unit and the
labelled containers). The gate showed one other heavy job before each launch.

## Results

| | ha-mcp 9.1.2 | ha-mcp 9.2.0 |
|---|---|---|
| Index digest | `sha256:381b1e01...ecb4` | `sha256:9de5bf6c...76ba` |
| Image revision label | `b35a3d22...` | `4d714830...` |
| Interpreter in the image | Python 3.13.18 | Python 3.14.10 |
| `serverInfo` | `ha-mcp` 9.1.2 | `ha-mcp` 9.2.0 |
| Negotiated protocol version (requested `2025-03-26`) | `2025-03-26` | `2025-03-26` |
| Tools | 77 | 77 |
| Catalog fingerprint | `7c8ee48bfedea7a992a04a3260757b66fe4734c65fd7e311f024733a52f7821d` | `0f1a2c75a766a111a83053733c6803ca14d60357137f4ecc7bc7978061982147` |
| Two captures byte-identical | yes | yes |
| `capture.json` SHA-256 | `ce522211...f3dd` | `ed3d6354...2d90` |
| `observation.json` SHA-256 | `6ff0b067...b164` | `8774ec05...abec` |
| Resource files (count, bytes, tree SHA-256) | 17, 299349, `5f5687ca...819e` | 17, 299349, `5f5687ca...819e` |
| Fixture mutation counters after the run | all empty | all empty |
| Containers and networks left behind | none | none |

The 9.1.2 catalog fingerprint equals the reviewed registry value for 9.1.2, so
the lane reproduces the admitted catalog from the admitted image.

Full values are in each `receipt.json`. In each directory `capture.json` is the
first of the two byte-identical captures, `observation.json` is the raw P1 and
P2 record (status, headers, body text and parsed messages), and
`image-resources.json` is the P7 inventory.

## What differs between the two images (mechanical)

`descriptor-diff-9.1.2-to-9.2.0.json` is produced by `descriptor_diff.py` from
the two directories. It states differences and reviews nothing.

- `initialize`: only `serverInfo.version` differs. Capabilities, the
  instructions text, the result keys and the response header names are equal.
- Tool names: none added, none removed; the wire order is equal.
- 17 of 77 descriptors differ, 60 are byte-equal. No `title`, `outputSchema`,
  `annotations` or `_meta` value differs.
  - Description only: `ha_config_list_helpers`, `ha_get_overview`,
    `ha_get_system_health`, `ha_get_zone`, `ha_manage_updates`.
  - Input schema only (parameter description text): `ha_config_set_automation`,
    `ha_config_set_dashboard`, `ha_config_set_scene`, `ha_config_set_script`
    (`python_transform`), `ha_get_history` (`start_time`), `ha_list_services`
    (`detail_level`, `service_fields`), `ha_report_issue` (`fields`),
    `ha_search` (`result_fields`), `ha_set_zone` (`icon`).
  - Input schema `anyOf` change: `ha_config_set_dashboard_resource` (`edits`),
    `ha_set_integration` (`config`, with its parameter description; the tool
    description also changes).
  - New parameters: `ha_config_set_helper` gains `abort`, `flow_id` and
    `menu_choice`; the `anyOf` of `config` changes; four parameter descriptions and the
    tool description change.
- Resource files: the 17 files under the image's `ha_mcp/resources/` are
  byte-equal between the images (same tree hash). The skills content did not
  change.
- Installed distributions: 16 differ, none added or removed. The versions
  agree with the lock-file reading in `../s3-vendored-and-dependencies.md`,
  including the three that move down (`httpx2`, `httpcore2`, `truststore`).
- The interpreter moves from Python 3.13 to 3.14. This comes from the image,
  not from the lock files, and is new information relative to S3.

Which of these differences matter for an admitted read or a governed write is
the review in S8; nothing is concluded here.

## Finding: the error shapes in these captures are not the reviewed ones

The 9.1.2 capture does not equal the committed
`docs/evidence/upstream-read-compatibility/ha-mcp-9.1.2.json`. Exactly two
values differ, both under `error_shapes.missing_registry_entity`:

| | Committed | This lane, 9.1.2 and 9.2.0 |
|---|---|---|
| `structured_code` | `SERVICE_CALL_FAILED` | `AUTH_INVALID_TOKEN` |
| `shape_fingerprint` | `3e1148ad...d9c8` | `4b23e6fe...067a` |

The tools, the catalog fingerprint and the other three error shapes are equal.
Cause, established from source and from the lane's peer log:

- The lane passes the peer the same environment as
  `prepare-ha-mcp-release-registry-update.yml`, including
  `HOMEASSISTANT_TOKEN=synthetic-release-registry-token`.
- The fixture accepts a WebSocket login only with its own constant,
  `synthetic-read-gateway-token` (the value `ci.yml` uses). Its REST read
  routes do not check the token. So REST probes behave as reviewed and every
  WebSocket connection is refused; the registry lookup behind
  `missing_registry_entity` is a WebSocket call.
- The committed 9.1.2 capture was not produced by the `capture` command. Its
  error shapes are the 9.1.0 values (`../../hamcp351/README.md`, lane 2, names
  a separate minimal fixture that is not in this repository), carried by the
  `import-runtime-capture` path, which copies error shapes from a baseline.

Consequences:

- The two captures here were taken in one configuration and are comparable
  with each other: their four error shapes are equal. That says the two images
  classify these four probes alike when the WebSocket login is refused. It does
  not show that 9.2.0 returns the reviewed `SERVICE_CALL_FAILED` shape for a
  missing registry entity. That comparison needs a peer whose WebSocket login
  succeeds and whose registry answers as Core does, and is made against the
  disposable Core in S6.
- For that reason no `ha-mcp-9.2.0.json` is written to
  `docs/evidence/upstream-read-compatibility/` in this step. The capture is
  kept here as S4 evidence; the registry capture and the contract review are
  produced in S8 from evidence whose error shapes are fit for the purpose.
- The token was not changed to make the captures match. The 9.1.2 run passed
  and was not repeated.
- Separate from this admission: with the token it passes today, the
  registry-update workflow's own capture step would record `AUTH_INVALID_TOKEN`
  for this probe, and `review_upstream_read_release.py` would then derive
  `entity_lookup_missing_resource_status: deterministic_entity_not_found` from
  a refused login. No workflow file is changed here; this is reported for a
  decision.

## Files

| Path | Content |
|---|---|
| `ha-mcp-9.1.2/`, `ha-mcp-9.2.0/` | `receipt.json`, `capture.json`, `observation.json`, `image-resources.json` for each image |
| `receipts/` | receipts of the two selftests and the pull, the lane's launch log, the gate's admission records |
| `descriptor_diff.py`, `descriptor-diff-9.1.2-to-9.2.0.json` | the comparison tool and its output |
| `SHA256SUMS` | SHA-256 of every other file in this directory |

Everything under `ha-mcp-*/` except `receipt.json` came out of a container that
ran an upstream image. It is untrusted data: read it with `python3 -I`, run
nothing from it. The peer and fixture logs are not committed.
