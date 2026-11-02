# Engineering 2.4.1-beta.58 release notes

Engineering 2.4.1-beta.58 (HAMCP-278) reads the signed Home Assistant Core
release registry from the repository's `registry` branch instead of `main`.
Only the branch segment of the code-owned URL changes. The same path, Core
public key, signed-journal verifier, cache and refresh cadence apply, and
there is still no URL option or override. It builds on 2.4.1-beta.57 (#343,
HAMCP-276), which includes 2.4.1-beta.56. No tool, input schema, route,
provider, persisted format, add-on option or Home Assistant write changes.

The same pull request adds an owner-gated one-tap signing and publication
workflow for that branch. It is repository tooling, not part of the image.

Acceptance: [`V2_4_1_BETA58_ACCEPTANCE.md`](V2_4_1_BETA58_ACCEPTANCE.md).

## Why

Every Core journal update used to need Josh to sign locally and then merge a
data-only pull request to `main`. Josh decided on 2026-11-02 to publish signed
journals to a dedicated `registry` branch through one approval in a protected
GitHub environment, with a separate job that pushes the signed journal. See
[`CORE_RELEASE_REGISTRY.md`](CORE_RELEASE_REGISTRY.md#one-tap-signing-and-registry-branch-publication-hamcp-278).

## The change

- **Runtime.** `CORE_REGISTRY_URL` is now
  `https://raw.githubusercontent.com/jeter-1/hass-mcp-admin/registry/upstream-trust/ha-core-release-registry.json`.
  The registry still accepts only journals signed by the configured Core key,
  with the existing chain, replay, expiry and revocation checks.
- **Workflow (repository only).**
  `.github/workflows/core-release-registry-publish.yml` runs from `main`:
  `verify` (read-only, no secret) → `sign` (protected environment, read-only)
  → `reverify` (read-only, no secret) → `publish` (`contents: write`, no
  secret, one non-force commit to `registry`). A dry run uses an ephemeral key
  and pushes nothing.
- **Owner tools.** `scripts/core_registry_seed_secret.py` (PEM to secret value,
  pipe-only) and `scripts/check_core_registry_branch.sh` (read-only branch
  checks), used by the
  [owner runbook](CORE_REGISTRY_ONE_TAP_RUNBOOK.md).

## Before deployment

Josh must create the `registry` branch byte-identical from `main` (runbook
step 2) **before** this release is deployed. Without the branch, refresh fails,
no new authority is learned, and unexpired cached authority stays usable until
it expires. The workflow refuses to sign while the branch is absent.

## Rollback

Reinstall 2.4.1-beta.57. It reads the `main` copy, which is frozen at the
cutover sequence. It keeps its cached authority until that expires and learns
no sequence published to `registry` afterwards, including revocations. No
persisted format changed.
