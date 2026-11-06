# Engineering 2.4.2-beta.5 release notes

Engineering 2.4.2-beta.5 (HAMCP-318) refreshes the image trust store for the
certifi/eMudhra incident (GHSA-6q48-6c7w-9gw2). It builds on 2.4.2-beta.4
(HAMCP-310, #371). The application code and the runtime Python lock are
identical to it.

Acceptance: [`V2_4_2_BETA5_ACCEPTANCE.md`](V2_4_2_BETA5_ACCEPTANCE.md).
Per-release records: [release index](RELEASES.md).

## The exposure

The runtime lock already pins certifi 2026.10.19 (2.4.1-beta.26), which is
fixed. Engineering's outbound HTTPS does not use certifi, though: aiohttp
builds its context with `ssl.create_default_context()`, which reads the
Debian system store, and no Engineering call passes its own SSL context. The
2.4.2-beta.3 image SBOM shows the following, and 2.4.2-beta.4 is built from
the same base index `78387bc3`:
- Debian `ca-certificates` 20250419, which still trusts the two roots above
  the compromised eMudhra intermediates;
- pip 25.0.1 from the base image, whose vendored certifi is affected;
- Python 3.12.14.

In the supported topology the Home Assistant and `ha-mcp` hops are local
plain HTTP, so no CA validation is involved there. The external TLS path is
the GitHub fetch of the signed Core and `ha-mcp` registries. Their Ed25519
signatures, sequence and freshness checks still refuse forged authority, but
an on-path interceptor with a forged certificate could block or delay
delivery. Practical risk is moderate to low; the fix is small.

## The change

- **Base image.** Both Docker stages move from `python:3.12-slim` index
  `78387bc3` to index `4840d514` (Python 3.12.16, Debian 13.7). Its
  `ca-certificates` 20250419+deb13u1 (trixie-security, 2026-10-20) removes
  `emSign ECC Root CA - G3` and `AAA Certificate Services` (Comodo) and adds
  nothing: the bundle goes from 150 to 148 roots, identically on amd64 and
  arm64. `build-inputs.json` records the new index, both platform manifests
  and configurations, and `python_version` 3.12.16.
- **Installer.** The new base still bundles pip 25.0.1. A new
  `installer.lock` pins exactly one pip 26.2.2 wheel by hash (vendored
  certifi 2026.10.19). The builder stage downloads it with the runtime
  wheels; the final stage installs it index-free before the runtime lock, and
  removes both wheel directories. `installer_version` is 26.2.2.
  `build_inputs.py source` refuses an installer lock with another package,
  more than one hash, no hash or a different version, and pip in the runtime
  lock is still refused. The in-image smoke and the publication SBOM check
  keep requiring exactly the runtime closure plus `pip == installer_version`,
  so the contract schema and the publication verifier are unchanged.
- **OS packages.** The digest refresh carries the Debian 13.7 point updates
  in the base. The package set is unchanged; the version changes, identical
  on both platforms, are recorded in
  [`evidence/hamcp318/base-image-review.json`](evidence/hamcp318/base-image-review.json)
  (among them `libssl3t64`/`openssl` deb13u3, `libc6` deb13u4, `tzdata`
  2026f and `apt` 3.0.4). The host-journal library `libsystemd0`
  257.13-1~deb13u1 is unchanged.

## Offline evidence

On a local amd64 build, with no network, a read-only root, all
capabilities dropped and no new privileges, the smoke reports Python 3.12.16,
pip 26.2.2, certifi 2026.10.19, 40 distributions with one pip, no leftover
wheel directories and `ca-certificates` 20250419+deb13u1.

The TLS probe ([`evidence/hamcp318/`](evidence/hamcp318/tls-probe-results.txt))
connects from the built image to the four public incident test hosts
(`dv-g3b-*-emudhra.we-have-a-problem.com`): Python `ssl` and aiohttp reject
all four with `unable to get local issuer certificate`, and both still
connect to `api.github.com` and `raw.githubusercontent.com` (HTTP 200). The
previous base accepts all six.

## Unchanged by design

- Application source, the runtime and test Python locks, tools, input
  schemas, routes, providers, signed profiles, contract fingerprints,
  persisted formats and add-on options.
- The build-inputs contract schema (version 1) and
  `verify_publication_source_image.py`.
- The CI test runner's Python (3.12.14 in the workflows); it is not the
  image runtime.

Static tools stay at 81. The advertised catalog stays at 109, or 110 with
the optional YAML reader.

## Known limitations

- An external TLS peer that still chains only to one of the two removed
  roots is no longer trusted. GitHub is unaffected.
- The arm64 image is built and smoked in CI only; the local TLS probe ran on
  amd64.
- The lock-preparation tool still targets pip 25.0.1 reports; that is a
  tooling review for the next lock regeneration.
- The known issues carried by 2.4.2-beta.4 are not addressed here.

## Rollback

Reinstall 2.4.2-beta.4. It has the same storage, plan and task formats.
Rolling back restores the affected trust store and installer.
