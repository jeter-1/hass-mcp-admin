# Upstream trust registry

The RC2dev9 registry is a signed data channel for exact upstream release
attestations. It is not a plugin system and cannot change executable policy.
Under
[`ADR-006`](architecture/ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md),
the current registry remains dashboard-specific. It is not a signed registry
for the 26 generic reads.

## Authority boundary

The Engineering binary owns the contract-family table, provider implementation,
tool allowlist, public tools, argument builders, output/hash validators, routes,
and fallback policy. The shipped table contains one family:
`ha_mcp_dashboard_read_v2`. Its only upstream tool is
`ha_config_get_dashboard`; its only public operations are `list_dashboards` and
`get_dashboard_config`.

A registry entry may bind an exact `ha-mcp` release to that existing family. An
exact-version entry, when present, is authoritative for that release. A
mismatch or revocation blocks it without falling back to an older release entry
or to unattested compatibility. When no exact-version entry exists, the
dashboard provider may independently admit an exact compiled family as
`admitted_compatible_contract`; that status makes no registry, provenance,
source, or image claim. If the optional registry is enabled, that absence must
be established from a currently usable signed registry.

An unknown `contract_family` is rejected while parsing. Registry data cannot
name an arbitrary endpoint, repository, image, tool, argument, operation,
provider, or Engineering capability. Thus a correctly signed record still
cannot activate `ha_set_entity`, `ha_set_device`, service/batch execution,
dashboard writes, a generic read, or any other uncompiled tool.

## Format and signature

The registry is strict UTF-8 JSON with schema version 1, monotonically increasing
integer `sequence`, UTC `generated_at`/`expires_at`, bounded `key_id`, and at
most 512 exact entries. Duplicate keys and unknown top-level fields are rejected.
The detached signature document specifies Ed25519, the matching key ID, and one
base64 signature over compact sorted-key UTF-8 JSON (`ensure_ascii=false`, no
non-JSON values).

Each entry includes exact identity/version, source tag/commit, official image
index and platform digests, image revision, compiled family, normalized
input/security/output/runtime fingerprints, optional catalog fingerprint,
review-evidence digest/time, and revocation flag.

RC2dev10 also permits four bounded informational fingerprints: raw input
schema, reviewed-security descriptor, reviewed fixture runtime descriptor, and
published runtime descriptor. They keep retained observability fields truthful
for the exact selected release. They cannot activate a contract family or
capability. Entries produced before RC2dev10 remain readable; absent
informational evidence is reported as unknown rather than being replaced with
another release's values.

Fixed fetch locations are repository-owned HTTPS URLs under
`jeter-1/hass-mcp-admin/main`. Redirects are disabled. Operators cannot configure
another URL or filesystem path.

## Runtime behavior

- Registry disabled: built-in exact-version entries remain authoritative when
  present; otherwise only exact compiled-family compatibility can be admitted,
  without an attestation claim.
- Registry enabled: validate the configured non-secret Ed25519 public key at
  startup, load an atomic last-known-good cache, then refresh no more often than
  every six hours.
- Cache: `/data/upstream-dashboard-trust-registry-cache.json`; maximum accepted
  age seven days and never exposed in an MCP response.
- Fetch: five-second connection limit, 15-second total limit, 256 KiB registry
  and 4 KiB signature bounds.
- Failure: preserve the last valid cache; never replace it with invalid data.
- Sequence: reject rollback and equal-sequence conflicting content.
- Expiry/revocation: reject the affected remote attestation before dashboard
  dispatch. A higher-sequence revocation overrides the same built-in release
  and remains deny-only after cache expiry until valid higher-sequence data
  supersedes it. Expired evidence cannot authorize a contract. Do not
  substitute an older attestation or the unattested path.
- No exact-version entry: a bounded refresh may first seek exact evidence; if
  none exists, evaluate only the binary-owned compiled family. When the
  registry is enabled this requires a currently usable signed registry; after
  hard expiry or registry unavailability, compatible-family admission is
  blocked. Exact semantic compatibility may otherwise report
  `admitted_compatible_contract`; incompatibility fails before `tools/call`.

Health exposes only bounded status, sequence, timestamps/ages, signature state,
cache state, refresh/failure category, admission source/status, attestation ID,
version and fingerprints. It never exposes registry content, signature bytes,
public-key value, endpoint path, URL, credentials, or raw exceptions.

Normalized and informational fingerprints have separate meanings. For an
exact-version entry, normalized input/security/output/runtime fingerprints are
authoritative and deliberately ignore only approved descriptive presentation
differences. Raw and descriptor fingerprints identify the reviewed published
representation and support drift diagnostics only. Without an exact entry, the
same binary-owned compiled semantics are evaluated directly and no release
evidence is inferred. A catalog fingerprint remains unrelated-tool
observability and is never a required-tool compatibility gate.

## Signing-key operations

The private seed exists only as
`UPSTREAM_TRUST_REGISTRY_SIGNING_KEY` in the protected GitHub environment
`upstream-attestation-signing`. The environment also holds the expected public
key and key ID. The workflow scopes the private key to the signing step; upstream
source inspection and disposable runtime execution do not receive it.

The runtime currently trusts one operator-configured public key. Rotation is a
two-release operation:

1. review a new public key and update the protected environment;
2. release an Engineering build configured to trust the new public key while the
   prior signed registry remains available through built-ins/LKG;
3. sign a higher-sequence registry with the new key;
4. confirm refresh, signature state and admissions;
5. revoke/remove the old private key from the protected environment.

Never place a private seed in add-on options, repository files, workflow output,
PR text, artifacts, cache data, or logs. A compromised key requires an explicit
Engineering/public-key rotation release; do not silently replace registry data.

## Manual workflow

`Prepare ha-mcp compatibility attestation` is `workflow_dispatch` on `main` and
accepts only an exact stable version. It requires protected-environment approval,
reviews fixed official source/image locations, uses the immutable image digest,
tests against disposable Home Assistant, validates semantic contracts and
hashes, signs a new higher-sequence entry, and opens a draft data-only PR.

The workflow has no package permission and cannot publish an Engineering image,
tag, release, or deployment. Normal review must verify the evidence/diff and run
CI before the data PR is merged. Promotion remains owned by the existing
Engineering release workflow.

## Deferred generic registry and automation

Dev15 does not reuse this dashboard registry as generic-read authority. Dev16
may define a separately reviewed signed evidence and revocation format for
binary-owned generic contract families, including monotonic sequence, cache and
expiry behavior, rollback/replay protection, revocation, and runtime refresh.
Dev17 may automate immutable source/image inspection, disposable runtime
extraction, catalog and annotation diffing, semantic fixtures, dashboard
contract checks, zero-write verification, compatibility reports, and draft
evidence updates. Neither generic registry authority nor that automation is
implemented by Dev15.

Signed data must remain unable to add a tool, change a classification, permit a
write or action, expand arguments, select a provider, or enable fallback.
