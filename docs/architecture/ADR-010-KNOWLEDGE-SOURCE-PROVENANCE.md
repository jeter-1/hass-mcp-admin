# ADR-010: Local knowledge source provenance

Status: accepted for 2.2.0-beta.4

## Context

Future evidence-backed documentation retrieval needs a trust boundary before it
can load source bundles or expose any retrieval behavior. Source text may be
incorrect, stale, malicious, or instruction-like. A source's own claims cannot
establish its trust class, version relevance, or authority.

K1 defines only an inert local provenance contract. It does not research,
download, recommend, plan, register a skill or MCP tool, alter routing, or load
the future skill bundle.

## Decision

An operator-selected local root may contain exactly one `manifest.json` using
schema version 1. The manifest identifies bounded UTF-8 text files with
canonical source IDs, explicit origin and classification metadata, retrieval
and optional expiry timestamps, an exact SHA-256 digest, a canonical
`knowledge:<source_id>` citation prefix, and independent Engineering, Home
Assistant, and integration version scopes.

Trust is a closed classification:

- `official_home_assistant`
- `reviewed_project_documentation`
- `reviewed_integration_documentation`
- `operator_supplied`
- `untrusted_reference`

Content is a closed classification:

- `documentation`
- `adr`
- `policy`
- `troubleshooting`
- `known_limitation`
- `release_note`
- `device_reference`

Version scopes are explicit `all`, `exact`, `range`, or `unknown` values.
Ranges have strict lower and upper bounds and explicit endpoint inclusion.
Integration scopes are independently `all`, one canonical integration plus a
version scope, or `unknown`. Missing target evidence and explicit unknown
scopes evaluate to `unknown`; they never become applicable by default.

The loader accepts only canonical child paths and the text suffixes `.adoc`,
`.markdown`, `.md`, `.rst`, and `.txt`. It resolves each path below the allowed
root, rejects escaping symlinks, missing or non-regular files, oversized files,
invalid UTF-8, unsupported formats, expiry, and digest mismatch. It rejects
unknown manifest fields and classifications, duplicate JSON keys and source
IDs, malformed timestamps, citations, IDs, and version ranges. Manifest
entries are sorted by source ID and hashed from a canonical metadata encoding,
so ordering and JSON formatting cannot change the manifest fingerprint.

Retrieved text is represented with role `data` and immutable false markers for
instruction authority and instruction execution. Neither trust classification
nor instruction-like source content can activate behavior. The K1 package has
no Engineering runtime, application, capability, tool-registry, provider,
routing, network, plan, or skill-bundle integration. It remains in the
repository-level `foundations` namespace until a separately versioned K2
integration is reviewed.

## Consequences

K2 may use this contract after separate review, but must supply a
configuration-trusted local root and preserve K1's inert-data and unknown
applicability semantics. K1 alone cannot retrieve documentation through MCP or
affect a recommendation.

Stable v1 remains unchanged. Engineering retains 48 locally registered tools,
26 expected delegated reads, and the 74-tool fully admitted contract. Shipping
the inert foundation advances the Engineering source version to
`2.2.0-beta.4`; there is no upstream admission change, fallback path, runtime
route, public schema, external access, or new dependency.
