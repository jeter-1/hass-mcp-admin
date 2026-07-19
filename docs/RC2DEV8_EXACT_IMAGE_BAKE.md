# RC2dev8 exact-image dependency-index bake

This manual acceptance closes the two dependency-index failure-injection gaps
that cannot be exercised safely against the deployed Home Assistant instance.
It does not publish, tag, build, or deploy an image.

## Safety boundary

The workflow is `workflow_dispatch` only and has `contents: read` permission.
It never logs in to GHCR and contains no image push or source-write operation.
The harness accepts no Home Assistant or MCP endpoint. It creates a private
Docker network, one pinned Home Assistant Core 2026.7.2 container, and the
published RC2dev8 Engineering image by immutable digest. All credentials are
synthetic and held only in process memory/environment.

The pinned Engineering artifact is:

`ghcr.io/jeter-1/hass-mcp-engineering-beta@sha256:e1c2edf06f03e12ca42e1c90f43aa5c9e5b226b17acb69d302c1f483ff789a4a`

The workflow verifies the dev8 index digest, all three platform manifests, the
selected platform config digest, and these OCI values before starting it:

- version `2.0.0-rc2-dev8`
- revision `c146c4378a221a34d66ee465772ecac09aca4899`
- created `2026-07-19T13:14:16Z`
- dirty `false`

## Acceptance sequence

1. Build generation 1 against synthetic Home Assistant configuration.
2. Wait beyond the five-second soft TTL but remain inside the 30-second hard TTL.
3. Stop only disposable Home Assistant.
4. Verify generation 1 returns immediately as explicitly stale evidence.
5. Verify the background refresh fails without publishing a partial generation.
6. Wait beyond the hard TTL and verify the old findings are refused.
7. Restore disposable Home Assistant and explicitly refresh.
8. Verify the generation advances exactly once and current evidence is restored.
9. Verify a subsequent warm lookup starts no build and makes zero HA requests.
10. Upload only the bounded, sanitized JSON evidence and always remove resources.

## Operator procedure

GitHub only permits a manually dispatched workflow after its workflow file is
present on the default branch. After this PR is reviewed and merged:

1. Open **Actions** in `jeter-1/hass-mcp-admin`.
2. Select **RC2dev8 exact-image TTL failure bake**.
3. Select **Run workflow** on `main` once.
4. Wait for the job to finish; do not rerun merely to improve timings.
5. Download `rc2dev8-exact-image-bake-evidence` and confirm `result` is `PASS`.
6. Record the workflow run URL with the RC2dev8 bake evidence.

The artifact is retained for seven days. Fixture timings are GitHub-hosted-runner
measurements and are not Raspberry Pi performance measurements.

## Separate running-container proof

This workflow proves the published exact image under failure injection; it does
not prove the digest of the container currently running on Home Assistant.
That remaining evidence requires either a Supervisor update/start log containing
the pulled digest or selected-field, read-only host Docker inspection. Never
capture full `docker inspect` output because it can contain environment secrets.
