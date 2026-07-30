# HA MCP Engineering Server 2.2.0-beta.3

Version `2.2.0-beta.3` adds the C1 signed compatibility-registry foundation as
an isolated data format and verifier. The compiled reviewed-release registry
remains the sole runtime compatibility authority.

## Signed registry foundation

C1 provides:

- strict, closed signed-envelope models;
- strict reviewed-release entry and revocation models;
- deterministic canonical serialization;
- exact registry content-digest calculation;
- Ed25519 signature verification over the canonical unsigned envelope;
- configured public trust-anchor selection by exact `key_id`;
- monotonic sequence enforcement;
- rollback rejection and idempotent replay detection;
- same-sequence replay-conflict rejection;
- conflicting duplicate and entry/revocation contradiction rejection;
- expiration and bounded future-timestamp enforcement;
- previous-registry digest chaining;
- strict per-tool, dashboard-attestation, provider-constraint, entry, and
  revocation validation;
- deterministic fixture and compiled-registry regeneration; and
- typed, bounded validation failures that do not expose rejected content.

Public trust anchors accept only canonical base64 Ed25519 public keys. Unknown
keys, malformed encodings, invalid signature lengths, invalid signatures, and
post-signature mutation fail closed. Production code contains no private
signing key or signing helper; test fixtures generate ephemeral test-only keys.
The existing pinned `cryptography==48.0.1` dependency supplies Ed25519 support.

## Runtime and authority boundary

This release does not add:

- runtime signed-registry loading;
- remote registry retrieval or a network client;
- startup activation, refresh, or filesystem watching;
- compatibility-admission changes;
- new MCP tools or public schemas;
- new health fields or capability metadata;
- provider access or routing;
- execution or approval authority;
- read or write paths;
- fallback behavior; or
- production signing keys.

No unverified registry data can affect a trust decision. A future integration
must separately review retrieval, accepted-state persistence, key rotation,
revocation retention, observability, operator recovery, and its relationship
to compiled authority.

## Compatibility

- Source catalog: 25 canonical plus 23 Engineering-native tools, 48 local
  registered tools.
- Configured exact admission may add 26 delegated reads for an expected 74
  live tools; 74 is not a source-frozen constant.
- Planned tools: 0.
- Task schema version: unchanged at 1.
- Reviewed upstream recommendation: `ha-mcp` 7.14.2, protocol `2025-03-26`.
- Compatibility entry: `ha-mcp-v7.14.2-7917b2d3`.
- Catalog fingerprint:
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
- Stable v1.1.2, F1 execution behavior, approval, dispatch, provider routing,
  public schemas, exact-image evidence, and zero fallback are unchanged.
