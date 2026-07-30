# ADR-009: Signed compatibility registry foundation

Status: accepted as an inert foundation for later 2.2 integration

## Context

Engineering currently admits generic upstream reads from the source-controlled
compiled reviewed-release registry. That authority binds exact upstream server,
version and protocol identity to source and image provenance, the stock catalog,
per-tool wire fingerprints and classifications, automatic-read decisions,
dashboard attestations, and argument restrictions.

A later release may distribute equivalent reviewed evidence remotely. Remote
distribution adds authenticity, rollback, replay, expiry, chain, key-rotation,
and revocation requirements. Those mechanics need an independently testable
format before any startup, retrieval, persistence, or admission integration is
considered.

## Decision

Add `ha_mcp_engineering.signed_registry` as a data-only package. C1 defines
strict closed models, deterministic serialization, an Ed25519 verifier, and a
bounded validation result. No current runtime component imports the package.

The signed envelope has these exact fields:

- `schema_version`
- `registry_id`
- `sequence`
- `generated_at`
- `expires_at`
- `previous_registry_sha256`
- `key_id`
- `entries`
- `revocations`
- `signature`

The signature is standard padded base64 containing exactly 64 Ed25519 signature
bytes. It covers canonical UTF-8 JSON for every envelope field except
`signature`. Canonical JSON sorts object keys, uses compact separators, retains
array order, emits Unicode directly, and rejects non-finite numbers. The
registry content digest is `sha256:` followed by lower-case SHA-256 hex over the
same canonical unsigned envelope. This preserves the repository's distinction
between prefixed content digests and unprefixed contract fingerprints.

Trust anchors are configured public Ed25519 keys selected by exact `key_id`.
Public-key base64 must decode canonically to exactly 32 bytes. An unknown key,
malformed encoding, incorrect length, invalid signature, or payload mutation
fails closed. Private signing keys and signing helpers are prohibited from the
production package; tests generate ephemeral private keys in the test-only
fixture module.

### Reviewed release evidence

Each entry retains the compiled registry terminology and evidence:

- `entry_id`, approval status, server name, version, and allowed protocols;
- source repository, release tag, source commit, review date and provenance;
- image index digest, architecture digests, and image revision;
- advertised stock catalog count and fingerprint;
- capture and policy resources, formats, and digests;
- all per-tool input, description, annotation, output, and runtime
  fingerprints;
- per-tool policy classification, automatic-read decision, quarantine reason,
  and argument restrictions;
- dashboard attestation identity, fingerprint, and compiled-constraint
  fingerprint;
- provider/tool argument-constraint fingerprints;
- error-contract and missing-resource semantics.

`entry_id` plus exact server/version/image identity is the revocation identity.
A revocation is a separate signed tombstone containing those fields, a canonical
UTC revocation timestamp, and a bounded reason. Duplicate entry identities,
duplicate exact releases, reused image digests, duplicate tombstones, or an
entry and tombstone for the same exact release are contradictions and are
rejected.

### Sequence, time, and chain rules

Timestamp fields use second-precision UTC `Z` form. Expiration must be later than
generation, and validation rejects an envelope at or after `expires_at`.
Generation may be at most 300 seconds ahead of the validator clock. The five
minute allowance matches the existing signed upstream-registry clock-skew
boundary and is intentionally fixed in this foundation.

The initial accepted registry is sequence 1 with a null previous digest. Later
integration must persist `registry_id`, sequence, and content digest from the
last accepted registry:

- a lower sequence is rollback and is rejected;
- the same sequence and digest is an idempotent replay;
- the same sequence with different content is a replay conflict;
- a higher sequence must name the accepted content digest in
  `previous_registry_sha256`;
- a registry ID change is rejected;
- a noninitial registry cannot bootstrap without accepted chain state.

All semantic, signature, time, sequence, and chain failures return one stable
error code without echoing registry content. Rejected results expose neither an
envelope nor a content digest.

## Runtime boundary

C1 does not:

- load a signed registry during server startup;
- retrieve data from a network or write a cache;
- replace or augment the compiled reviewed-release authority;
- alter compatibility admission, fingerprints, fallback behavior, provider
  routing, upstream exposure, health, capabilities, or public MCP schemas;
- add tools or change the source-frozen 25 canonical, 23 Engineering-native,
  and 48 local registered tool contract, or the configured deployment
  expectation of 26 delegated reads and 74 live tools when exact upstream
  admission is present; or
- provide production signing material.

An integration PR must separately design retrieval, durable accepted-state
storage, key distribution and rotation, revocation retention, observability,
operator recovery, and the exact relationship to compiled authority. That work
requires its own security review and negative-reachability evidence.

## Consequences

The remotely distributable format and verifier can be reviewed and exercised
without making unverified data authoritative. Current compatibility registry
generation, exact-image fixtures, admission, and failure behavior remain
unchanged. Future integration may consume only an accepted validation result
and must continue to keep executable policy and provider reachability compiled
and reviewed.
