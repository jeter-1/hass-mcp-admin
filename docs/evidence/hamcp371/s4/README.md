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
