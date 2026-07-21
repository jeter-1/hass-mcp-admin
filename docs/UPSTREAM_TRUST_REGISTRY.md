# Upstream trust registry

The RC2dev9 registry is a signed data channel for exact upstream release
attestations. It is not a plugin system and cannot change executable policy.

## Authority boundary

The Engineering binary owns the contract-family table, provider implementation,
tool allowlist, public tools, argument builders, output/hash validators, routes,
and fallback policy. The shipped table contains one family:
`ha_mcp_dashboard_read_v2`. Its only upstream tool is
`ha_config_get_dashboard`; its only public operations are `list_dashboards` and
`get_dashboard_config`.

A registry entry may bind an exact `ha-mcp` release to that existing family. An
unknown `contract_family` is rejected while parsing. Registry data cannot name
an arbitrary endpoint, repository, image, tool, argument, operation, provider,
or Engineering capability. Thus a correctly signed record still cannot activate
`ha_set_entity`, `ha_set_device`, service/batch execution, dashboard writes, or
any other uncompiled tool.

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
for the exact selected release. They are not consulted by `decide_admission`
and cannot activate a contract family or capability. Entries produced before
RC2dev10 remain readable; absent informational evidence is reported as unknown
rather than being replaced with another release's values.

Fixed fetch locations are repository-owned HTTPS URLs under
`jeter-1/hass-mcp-admin/main`. Redirects are disabled. Operators cannot configure
another URL or filesystem path.

## Runtime behavior

- Registry disabled: built-in entries only.
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
  while the signed registry remains valid.
- Unknown release: make one bounded refresh/revalidation attempt before failing
  closed. No `tools/call` occurs before admission.

Health exposes only bounded status, sequence, timestamps/ages, signature state,
cache state, refresh/failure category, admission source/status, attestation ID,
version and fingerprints. It never exposes registry content, signature bytes,
public-key value, endpoint path, URL, credentials, or raw exceptions.

Normalized and informational fingerprints have separate meanings. Normalized
input/security/output/runtime fingerprints deliberately ignore approved
descriptive presentation differences and are authoritative for admission. Raw
and descriptor fingerprints identify the reviewed published representation and
support drift diagnostics only. A catalog fingerprint remains unrelated-tool
observability and is never a required-tool compatibility gate.

## Signing-key and sequence operations

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

`scripts/manage_upstream_trust_registry.py` is the fixed-path, repository-side
operator. It is not imported by the add-on and is not an MCP tool. It supports:

- `bootstrap`: create sequence 1 only when neither committed registry file
  exists and add exactly one fully reviewed release;
- `add`: append one previously unknown exact server/version/family identity;
- `revoke` and `restore`: change only the selected entry's boolean revocation
  state;
- `renew`: keep the entries byte-for-byte semantically equal while updating the
  validity window; and
- `verify`: check canonical files, schema, public-key signature, key ID,
  sequence, every entry/evidence digest and the generated index without a
  private key or a write.

Every mutation derives `next_sequence = current_sequence + 1`, requires the
operator's expected current sequence, signs compact sorted-key UTF-8 JSON, and
verifies the proposal before atomically replacing each fixed output. `--dry-run`
performs the same construction and verification without creating files. There
is no sequence override, registry URL, output-path, capability, tool, family or
runtime argument. The repository newline is stored for review but is not part
of the Ed25519 signed bytes.

The data outputs are limited to the registry JSON, detached signature JSON, one
bounded review-evidence document and the generated index. Machine-readable
stdout contains bounded identities, sequence/timestamps and digests, never key
material.

## Manual workflow

`Prepare ha-mcp compatibility attestation` is `workflow_dispatch` on `main` and
offers `bootstrap`, `add`, `revoke`, `restore`, `renew`, and `verify`. Every
mutation requires an expected current sequence; add/bootstrap also require an
exact stable version and execute the existing fixed official source, image and
disposable-runtime inspection. Revoke/restore/renew preserve unrelated entries
and produce an exact bounded semantic diff. Verify receives only the public key
and creates no branch or PR.

Mutations require approval in `upstream-attestation-signing`. Only the signing
step receives the private seed. A repository-wide concurrency group prevents two
operations racing. The generated branch is checked against a four-file data-only
allowlist and the workflow opens a draft PR whose body records old/new sequence,
affected identity/revocation state, validity window, key/family, relevant source
and image evidence, and evidence digest.

The workflow has no package permission and cannot publish an Engineering image,
tag, release, or deployment. Normal review must verify the evidence/diff and run
CI before the data PR is merged. Promotion remains owned by the existing
Engineering release workflow.

`reviewed_at` is part of signed release evidence. The runtime accepts only a
valid ISO-8601 timestamp with explicit UTC (`Z` or `+00:00`), canonicalizes it to
`Z`, and rejects naive values, non-UTC offsets and invalid calendar dates before
admission. This validation does not change normalized contract semantics.

## Accepted operating limitations

- Production registry and signature URLs remain fixed; redirects are disabled.
- There is no live outage-injection control. Bad signatures, unavailable remote
  data and the seven-day hard-cache boundary are exercised only in the disposable
  test harness.
- The monotonic rollback anchor is the LKG cache under persistent `/data`.
  Erasing `/data` removes that local anchor, so backup and restore of `/data`
  requires a separately governed operational policy.
- A lower sequence is never a recovery. Correct bad data, revocation or expiry
  with a separately reviewed higher sequence.
- Production use must not begin before the protected GitHub environment,
  required reviewers, key custody and offline recovery copy exist.
