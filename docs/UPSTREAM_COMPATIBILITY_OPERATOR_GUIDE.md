# Upstream compatibility operator guide

Use this procedure only for a stable `homeassistant-ai/ha-mcp` release whose
dashboard-read implementation is believed to remain inside the compiled
`ha_mcp_dashboard_read_v2` family.

## One-time setup

1. Generate an Ed25519 seed and public key in a protected administrative
   environment. Do not use a shell command that echoes the seed.
2. Create the GitHub environment `upstream-attestation-signing` with required
   reviewers and no untrusted branch access.
3. Add environment secrets:
   `UPSTREAM_TRUST_REGISTRY_SIGNING_KEY` (base64 raw 32-byte seed),
   `UPSTREAM_TRUST_REGISTRY_PUBLIC_KEY` (base64 raw public key), and a bounded
   `UPSTREAM_TRUST_REGISTRY_KEY_ID`.
4. Put only the public key into the Engineering Beta add-on option
   `upstream_trust_registry_public_key`, then explicitly enable
   `upstream_trust_registry_enabled` in a later deployment window. Do not repeat
   the key or endpoint in tickets/logs.

## Review a release

1. Open GitHub Actions on `main` and select
   **Prepare ha-mcp compatibility attestation**.
2. Enter only the exact stable version, such as `7.14.2`.
3. Approve the protected environment after confirming the upstream release is
   intentional.
4. The workflow resolves the exact official tag/source and GHCR image, verifies
   image/source ancestry and allowed metadata-only packaging delta, records
   amd64/arm64 platform digests and provenance, starts the exact image by digest
   against disposable Home Assistant, and extracts the actual MCP contract.
5. The workflow invokes only dashboard inventory and exact config reads with
   `include_screenshot=false`; it verifies the dashboard hash and records zero
   write dispatches.
6. Semantic normalization must match the compiled family. Descriptive changes
   may normalize away; argument, annotation, output/hash, protocol or unknown
   semantic changes fail.
7. The workflow signs a new entry and opens a draft PR containing only registry,
   signature, bounded evidence and generated index files.

## Review the data PR

Confirm exact version, source commit/tag, official immutable image/index/platform
digests, image revision/created time, provenance/SBOM result, runtime identity,
ordinary catalog fingerprint, all four normalized fingerprints, fixed argument
shapes, dual-hash evidence and zero write dispatches. Confirm the sequence
increased once and no prior entry was silently replaced.

Reject the PR if the release adds a required argument, changes a type/default,
loosens `additionalProperties`, changes safety annotations, adds an output schema,
changes the dashboard return/hash contract, changes the protocol, or cannot prove
the exact source/image relationship. Do not whitelist a fingerprint to bypass a
compiled-family rejection.

The PR must not change runtime code, workflows, Dockerfiles, dependencies,
Engineering versions, public schemas, tool allowlists, capability metadata, or
governance. Merge remains a separate human action.

## Revocation and recovery

Normal attestation creation cannot replace or re-add an existing/revoked release.
Revocation requires a separately reviewed higher-sequence data change signed by
the protected key. After merge, verify runtime health reports the new sequence
and `rejected_revoked_attestation` before relying on it.

On refresh/signature/expiry failure, do not delete the cache or disable security
checks to restore access. Built-in exact releases continue to work; a future
registry-only release remains unavailable until valid data is restored. Compare
the bounded failure category, repository registry/signature files, protected key
ID/public key, sequence and expiry. Never paste registry private key material into
debug output.

## Deferred registry administration

The upstream 7.14.1 `ha_set_entity` and `ha_set_device` contracts are retained as
non-runtime design evidence only. They are destructive and cannot be activated by
a signed registry entry. A future governed registry-administration milestone must
separately design proposal, external approval, stale-state, apply, verification,
rollback and audit semantics before either operation can enter Engineering.
