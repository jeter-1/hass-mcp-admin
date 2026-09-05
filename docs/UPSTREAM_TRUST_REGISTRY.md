# Upstream trust registry

The RC2dev9 registry is a signed data channel for exact upstream release
attestations. It is not a plugin system and cannot change executable policy.
Under
[`ADR-006`](architecture/ADR-006-CONTRACT-LEVEL-UPSTREAM-COMPATIBILITY.md),
the original registry remains dashboard-specific. It is not generic-read
authority. ADR-023 defines a separate signed journal for binary-owned delegated
read profiles; neither registry can create executable behavior.

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
or to self-advertised compatibility. When no exact-version built-in or verified
signed entry exists, dashboard admission fails closed even if the live
descriptor matches a compiled family.

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

- Registry disabled: a built-in exact-version entry is required and remains
  authoritative. A release without one is unavailable.
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
- No exact-version entry: a bounded refresh may seek exact signed evidence. If
  none exists, admission fails closed before `tools/call`; the binary-owned
  compiled-family match cannot authorize the release by itself.

Health exposes only bounded status, sequence, timestamps/ages, signature state,
cache state, refresh/failure category, admission source/status, attestation ID,
version and fingerprints. It never exposes registry content, signature bytes,
public-key value, endpoint path, URL, credentials, or raw exceptions.

Normalized and informational fingerprints have separate meanings. For an
exact-version entry, normalized input/security/output/runtime fingerprints are
authoritative and deliberately ignore only approved descriptive presentation
differences. Raw and descriptor fingerprints identify the reviewed published
representation and support drift diagnostics only. Without an exact entry, the
release is unavailable even if the same binary-owned compiled semantics appear
to match. A catalog fingerprint remains unrelated-tool observability and is
never a required-tool compatibility gate.

The compiled generic/provider release registry separately binds exact runtime
authority. Its reviewed 8.1.0, 8.1.1, 8.2.0, 8.4.1, and 8.4.3 entries record the
tag target, standalone index and platform manifests, add-on index/image
manifests, strict full-contract fingerprint, full 78-tool policy, and
release-declared runtime model. Its raw
standalone catalog fingerprint is diagnostic; complete per-tool semantic
validation remains authoritative. The entry excludes release-page executables
and MCPB that advertise 8.0.0 and cannot inherit 8.1.0 trust from their asset
names.

The 8.1.1 entry additionally binds the review-time compatibility-family
decision described by
[`ADR-007`](architecture/ADR-007-COMPATIBILITY-FAMILY-ADMISSION.md). That family
record is not signed wildcard authority and cannot admit an unlisted release.

The exact 8.2.0 entry is a full independent review rather than an 8.1.x family
admission. It binds the annotated tag object, source commit/tree/archive,
standalone and add-on OCI identities, the complete 78-tool catalog, and the
corrected existing-hyphenless-dashboard resolver behavior. It grants no
authority to 8.2.x ranges or future patches.

The exact 8.4.1 and 8.4.3 entries independently bind their annotated tag
objects, source commits and trees, standalone and Home Assistant App OCI
identities, exact 78-tool image catalogs, four error-envelope probes, and
per-tool policies. Error
compatibility is capability-scoped under a binary-owned adapter: a changed
search validation envelope cannot suppress unrelated reads. Registry data may
select that compiled behavior but cannot define error mappings. Dashboard
authority remains separately reviewed and attested for each exact release.
Backup and lifecycle surfaces remain held; only reviewed read-gateway and
dashboard surfaces are admitted.

The source tag's Home Assistant add-on `config.yaml` also remains diagnostic
build input. For operational lifecycle identity, Supervisor's exact
endpoint-bound installed inventory is authoritative and must agree with the
admitted MCP initialize identity. Neither a tagged-tree version nor an OCI
revision label may override that runtime binding.

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

`Prepare ha-mcp release-registry update` is a separate manual workflow for the
ADR-023 generic-read journal. It runs only from protected `main`, accepts one
exact stable version, resolves fixed official source and image locations, and
uses a protected environment for human approval and the Ed25519 private key.
The private key is scoped to the signing step. Before signing, the workflow
captures the exact image catalog twice against a disposable read-only Home
Assistant fixture and rejects any mutation evidence.

The resulting draft pull request may contain only the signed journal, one
bounded compatibility-evidence record, and its generated index. The signed
entry selects one already compiled profile; unmatched capabilities remain held
and unknown capabilities remain unsupported. It explicitly quarantines
dashboard authority, which requires its separate exact-release attestation.
The workflow cannot merge, publish an Engineering image, deploy, restart, or
mutate Home Assistant.

## Separate generic registry

The ADR-023 generic journal uses monotonic sequence and digest linkage, bounded
checkpointing, expiry, rollback/replay protection, denial-only revocations, and
atomic cache replacement. Signed data remains unable to add a tool, change a
classification, permit a write or action, expand arguments, select a provider,
or enable fallback. A new schema, semantic contract, provider behavior, or
adapter still requires an Engineering release.
