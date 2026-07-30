# 2.2.0-beta.4 acceptance contract

Version: `2.2.0-beta.4`

Source baseline:
`b38fad90dfdf50af3ad3a3f4a12e67093c5432cf`

This is the source and later operator-controlled acceptance contract for the K1
knowledge-provenance foundation. Source implementation and validation must not
access live Home Assistant, load knowledge at runtime, publish an image, create
a tag or release, merge, or deploy.

## Immutable boundaries

- The K1 package remains in the repository-level `foundations` namespace and
  has no production startup or application import.
- Knowledge content is untrusted, instruction-inert data. Provenance trust
  classes grant no recommendation, plan, approval, execution, provider, or
  compatibility-admission authority.
- The compiled reviewed-release registry remains authoritative. K1 does not
  integrate with the C1 signed-registry format or trust anchors.
- No local-root configuration, loader, remote retrieval, watcher, network
  client, Home Assistant read, tool, capability, health field, provider route,
  write path, or fallback is added.
- Public MCP schemas, task schema version 1, plans, approvals, dispatch,
  operation-specific verification, and provider behavior are unchanged.
- The source catalog remains 25 canonical plus 23 Engineering-native tools,
  48 local registered tools. Exact configured admission may add 26 delegated
  reads for an expected 74 live tools.
- Stable v1.1.2 is unchanged.

## Manifest and provenance acceptance

Require strict schema and required-field validation, unknown-field rejection,
exact types and bounds, duplicate JSON-key and source/document rejection,
deterministic ordering and fingerprints, and immutable caller input.

Require closed provenance/trust, content, redaction, version, and integration
classifications. Missing or explicit unknown relevance must evaluate to
`unknown`, never implicitly current, compatible, trusted, or recommended.
Expiration and contradictory version evidence must fail closed.

Require canonical relative child paths, absolute and traversal rejection,
resolved-path containment, escaping-symlink rejection where supported, bounded
approved UTF-8 formats, maximum sizes, and exact content SHA-256 verification.
Every citation must retain validated source identity, document identity,
version evidence, relative path, digest, and bounded location provenance.

Embedded instruction-like text must retain data role with no instruction,
authorization, recommendation, plan, approval, or execution semantics.

## Required validation

Run and record:

- the focused K1 suite twice;
- two buffered and one verbose complete unittest discoveries;
- compilation, metadata, YAML, dependency consistency, secret, PowerShell,
  protected-path, whitespace, Full, and exact-head Evidence gates;
- strict Engineering dependency audit;
- deterministic compatibility-registry validation and regeneration/drift
  checks;
- disposable Home Assistant contracts;
- exact-image `ha-mcp` 7.14.1 and 7.14.2 lanes;
- stable-v1 and Engineering image builds; and
- amd64, arm64, and arm/v7 no-push builds where supported.

No failing test may be weakened, skipped, or converted to an expected failure.
The Engineering image must report `2.2.0-beta.4`.

## Later integration and rollback

Runtime root selection, loading, indexing, retrieval, citation presentation,
observability, recommendation policy, and any relationship to C1 require a
separate reviewed milestone. Beta 4 grants none of that authority.

Rollback to accepted `2.2.0-beta.3` requires no knowledge-state migration
because Beta 4 creates no runtime knowledge state. Rollback does not authorize
redispatch, fallback, or evidence rewriting.
