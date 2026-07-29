# 2.2.0-beta.3 acceptance contract

Version: `2.2.0-beta.3`

Source baseline:
`3e5d70953483b1d49cd8561f4de26764d118a21e`

This is the source and later operator-controlled acceptance contract for the C1
signed compatibility-registry foundation. Source review and CI must not access
a live Home Assistant instance, approve or dispatch an operation, publish an
image, create a release or tag, merge, or deploy.

## Immutable boundaries

- The compiled reviewed-release registry remains authoritative for runtime
  compatibility admission.
- No signed-registry loader, remote retrieval, startup activation, persistence,
  refresh loop, tool, health field, capability, provider route, execution
  authority, write path, or fallback is added.
- No production private signing key or signing helper exists.
- Public MCP schemas, task schema version 1, plan hashes, approvals, dispatch,
  operation-specific verification, and provider behavior are unchanged.
- The source catalog remains 25 canonical plus 23 Engineering-native tools,
  48 local registered tools. Exact configured admission may add 26 delegated
  reads for an expected 74 live tools.
- Stable v1.1.2 is unchanged.

## Format and verification acceptance

Validate strict closed envelope, reviewed-release entry, and revocation models.
Require deterministic canonical unsigned serialization and content digests,
strict canonical base64 and Ed25519 lengths, exact trust-anchor selection, and
signature verification before accepted data is returned.

Prove fail-closed rejection for unknown schema versions, fields, and keys;
malformed digests and signatures; invalid signatures; signed-payload mutation;
expired and materially future registries; sequence rollback; same-sequence
conflicts; broken previous-digest chains; duplicate entries and revocations;
and entry/revocation contradictions. Same-sequence same-digest replay must be
idempotent. Failure output must be typed, deterministic, bounded, and omit
rejected content.

Each reviewed release must retain exact server, version, protocol, source,
image, catalog, per-tool policy, automatic-read, dashboard-attestation,
provider-constraint, and revocation identity evidence from the compiled
registry terminology.

## Required validation

Run and record:

- the focused signed-registry suite twice;
- compiled-registry, attestation, exact-image-harness, and publication tests;
- deterministic registry regeneration and drift checks;
- two buffered and one verbose complete unittest discoveries;
- compilation, metadata, YAML, dependency consistency, secret, PowerShell,
  protected-path, whitespace, Full, and exact-head Evidence gates;
- strict Engineering dependency audit;
- disposable Home Assistant contracts;
- exact-image `ha-mcp` 7.14.1 and 7.14.2 lanes;
- stable-v1 and Engineering image builds; and
- amd64, arm64, and arm/v7 no-push builds.

No failing test may be weakened, skipped, or converted to an expected failure.
The Engineering image must report `2.2.0-beta.3`.

## Later integration and rollback

Runtime retrieval, accepted-state storage, trust-anchor distribution and
rotation, revocation retention, observability, operator recovery, and any
admission relationship require a separate reviewed integration. This release
grants none of that authority.

Rollback to the accepted `2.2.0-beta.2` artifact requires no signed-registry
state migration because Beta 3 does not load or persist signed registries.
Rollback does not authorize redispatch, fallback, or historical evidence
rewriting.
