# Engineering 2.4.2-beta.4 acceptance

This contract covers HAMCP-318: the Engineering image trust store is refreshed
for the certifi/eMudhra incident (GHSA-6q48-6c7w-9gw2). It follows
2.4.2-beta.3 (HAMCP-311, #366).

See also the release notes:
[`V2_4_2_BETA4_RELEASE_NOTES.md`](V2_4_2_BETA4_RELEASE_NOTES.md).

**Release target.** `2.4.2-beta.4`, following 2.4.2-beta.3. The release
transition accepts only `2.4.2-beta.3 -> 2.4.2-beta.4` from the current base.

**Merge-order rule.** HAMCP-310 (#371) also stages 2.4.2-beta.4 and is next
in line, and the 2.4.2 Lows bundle (#373) stages it as well. If another
release PR merges ahead of this one, this branch:
1. merges `main` with a merge commit;
2. restages once to the next free beta, renaming these documents;
3. runs final Evidence once, when it is next in line.

The release is materialized in all three version authorities, with no staged
declaration left. Nothing is published or deployed.

The contract has four stages:
1. source and offline acceptance;
2. release CI;
3. publication;
4. installed read-only acceptance.

It authorizes none of the later stages.

## 1. Source and offline acceptance

Bind:
- **The outcome**, from the HAMCP-318 register row: the published image
  carries Debian `ca-certificates` 20250419+deb13u1 or later, pip 26.2.2 or
  later and Python 3.12.16 or later, keeps certifi 2026.10.19 or later, shows
  those versions in the SBOM on both architectures, and its aiohttp (system
  store) rejects the incident test chains while still connecting to GitHub.
  No other runtime change. Priority High, filed by the architect; Josh may
  override.
- **The cause**, at the base `62c6434f`:
  - `hass_mcp_engineering_beta/Dockerfile` pins `python:3.12-slim` index
    `78387bc3` in both stages, which carries `ca-certificates` 20250419,
    pip 25.0.1 and Python 3.12.14;
  - aiohttp uses the system store (`ssl.create_default_context()`), so the
    already-fixed certifi in the runtime lock does not cover outbound TLS.
- **The base authority.** The new index `4840d514`, its two platform
  manifests and configurations, and the per-platform Debian package lists,
  read from the registry and the image layers without running arm64, are in
  `docs/evidence/hamcp318/base-image-review.json`, with the Debian changelog
  entry for `ca-certificates` 20250419+deb13u1 and the removed root
  fingerprints.
- **The installer authority.** PyPI's JSON metadata for
  `pip-26.2.2-py3-none-any.whl` (sha256 `fd7f4dfa…13a70`, uploaded
  2026-10-20, vendored certifi 2026.10.19), recorded in the same file.
- **The non-goals:** no application source, runtime or test lock, tool,
  schema, route, provider, profile, persisted-format or add-on option
  change; no change to the build-inputs schema, the publication verifier or
  the CI workflows.
- `2.4.2-beta.4` materialized before Ready, with exact document resolution.
- The PR head SHA, its `validate` run and the Evidence tier log named in the
  PR.

The review needs a security section covering:
- **Exact inputs only.** The base moves by digest, never by tag. The
  installer is one hash-pinned wheel fetched with `--require-hashes
  --only-binary=:all:` from the explicit PyPI index and installed with
  `--no-index`. No `--upgrade`, `ensurepip` or `get-pip` path exists, and
  `build_inputs.py source` refuses an installer lock of any other shape.
- **Inventory unchanged in kind.** The final image still contains exactly the
  runtime closure plus one pip at `installer_version`, checked by the smoke
  and by the publication SBOM verifier on both architectures.
- **No new reachability.** No new tool, route, dispatch, write or fallback.
  Removing two roots can only refuse TLS peers that chain to them.
- **Disclosed OS drift.** The Debian point updates carried by the new base
  are listed per platform; the package set and the host-journal library are
  unchanged.

Required offline evidence:
- `tests/test_build_inputs.py`: the contract pins installer 26.2.2 or later,
  Python 3.12.16 or later and certifi 2026.10.19 or later, and matches the
  review file's index, Python, installer and platform digests; the review
  records `ca-certificates` 20250419 -> 20250419+deb13u1 on both platforms
  and exactly the two removed roots. The installer lock refuses a wrong
  version, two hashes, an extra package, a non-pip package, no hash, a range,
  an empty file and a missing file, and pip in the runtime lock is still
  refused. The Dockerfile installs `installer.lock` before
  `requirements.lock` from `/installer`, removes both wheel directories and
  has no `--upgrade`, `ensurepip` or `get-pip`;
- `tests/test_hamcp224_host_journal.py`: the host-journal fixture's base is
  bridged to the new index through the review, with `libsystemd0`
  257.13-1~deb13u1 unchanged on both platforms;
- the publication-source verifier, rc1 publication and provenance, CI
  efficiency, packaging-boundary and deployment-metadata suites pass;
- a local amd64 build whose hardened smoke reports Python 3.12.16, pip
  26.2.2, certifi 2026.10.19 and `ca-certificates` 20250419+deb13u1, and the
  TLS probe in `docs/evidence/hamcp318/tls-probe-results.txt`;
- the complete repository suite in the Evidence tier.

## 2. Release CI

The exact materialized head's `validate` check passes, including the
release transition and the packaging job, which builds and smokes both
architectures with no network, a read-only root, dropped capabilities and no
new privileges, and retains both inventories.

## 3. Publication

Publication happens only through Josh's Ready action and the repository's
head-bound workflow. The publisher's SBOM check must show, on both
architectures, Python 3.12.16, pip 26.2.2 and certifi 2026.10.19, and the
provenance must name base index `4840d514` or its child digest.

## 4. Installed read-only acceptance (separately authorized, after deploy)

This stage runs only after Josh separately authorizes deploying this
release. It is the architect's and read-only.

1. **Identity.** `server_info` and `get_server_health` report
   `2.4.2-beta.4`, the merged release SHA as build SHA and `dirty: false`.
2. **Trust store.** The published image's SBOM (from the release
   attestation, not the running container) lists `ca-certificates`
   20250419+deb13u1 or later, pip 26.2.2, Python 3.12.16 and certifi
   2026.10.19 for both architectures.
3. **Registry delivery.** The signed Core and `ha-mcp` registry state the
   server already reports through its read-only health and capability reads
   is the same as before the upgrade, and the add-on log shows no new TLS
   verification or registry fetch failure.
4. **Tool catalog.** A fresh `tools/list` shows the same tool names and
   descriptors as 2.4.2-beta.3.

PASS needs every check. If a step fails, record the response in HAMCP-318.
Whether to roll back is Josh's decision.
