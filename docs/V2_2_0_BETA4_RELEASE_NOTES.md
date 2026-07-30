# HA MCP Engineering Server 2.2.0-beta.4

Version `2.2.0-beta.4` adds the K1 knowledge-provenance foundation as an
isolated, local, runtime-inert data contract. It does not retrieve, load, or
act on knowledge through the Engineering server.

## Knowledge provenance foundation

K1 provides:

- strict schema-versioned knowledge manifests with required-field and
  unknown-field rejection;
- closed provenance-only trust, content, redaction, version, and integration
  classifications;
- deterministic parsing, source ordering, canonical metadata, and manifest
  fingerprints;
- canonical child-path enforcement with traversal, absolute-path, and resolved
  symlink-escape rejection;
- bounded UTF-8 text formats and file sizes;
- exact SHA-256 content verification;
- duplicate source/document rejection and deterministic version-conflict
  handling;
- explicit expiration and independent Engineering, Home Assistant, and
  integration relevance;
- exact source, document, version, path, digest, and bounded citation
  provenance; and
- immutable instruction-inert text markers.

Missing or explicit unknown version relevance remains `unknown`. It does not
become implicitly current, compatible, trusted, or recommended. Trust class
describes provenance only and grants no execution or admission authority.

## Runtime and authority boundary

This release does not add:

- startup loading, filesystem watching, or remote retrieval;
- a network client or Home Assistant access;
- MCP tools, capabilities, health fields, or provider routes;
- recommendation, change-plan, approval, or execution authority;
- compatibility admission or signed-registry integration;
- writes, generic actions, or fallback; or
- a runtime dependency.

Knowledge text remains untrusted data. Embedded instruction-like content
cannot become system instructions, tool permission, approval, or execution
authority.

## Compatibility

- Source catalog: 25 canonical plus 23 Engineering-native tools, 48 local
  registered tools.
- Configured exact admission may add 26 delegated reads for an expected 74
  live tools.
- Planned tools: 0.
- Task schema version: unchanged at 1.
- Reviewed upstream recommendation: `ha-mcp` 7.14.2, protocol `2025-03-26`.
- Compatibility entry: `ha-mcp-v7.14.2-7917b2d3`.
- Catalog fingerprint:
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
- Stable v1.1.2, C1 signed-registry authority, F1 execution behavior, approval,
  dispatch, provider routing, public schemas, and zero fallback are unchanged.

Rollback to the accepted `2.2.0-beta.3` artifact requires no knowledge-state
migration because Beta 4 does not load or persist knowledge manifests.
