# Engineering 2.2.0-beta.43 acceptance

Beta 43 stages a narrow confidentiality correction for persisted-plan
pagination cursors. Engineering 2.2.0-beta.42 remains the advertised version
until a separately authorized protected promotion. Stable v1.1.2 and the
51-tool Engineering registration remain unchanged.

This candidate changes only the private serialization of continuation cursors
returned by `get_change_plan`. It does not change the public input schema,
output field names, persisted plans, plan observability summaries, helper-risk
classification, policy, approval authority, locking, dispatch, or execution.

## Source and release authority

1. Resolve the feature base to current `origin/main` and require the reviewed
   Beta 43 implementation head and a clean worktree.
2. Require advertised Engineering `2.2.0-beta.42`, staged Engineering
   `2.2.0-beta.43`, stable `1.1.2`, and exact staged-document resolution to this
   document and the matching Beta 43 release notes.
3. Require `.release/next-version` to contain exactly `2.2.0-beta.43`. The
   advertised authorities in `config.yaml`, `version.py`, and the metadata
   validator must remain Beta 42 in the feature pull request.
4. Require public schema fingerprints and the 51-tool count to remain exact,
   with no registration, annotation, held-status, routing, provider-admission,
   fallback, workflow, Dockerfile, `repository.yaml`, or deployment-metadata
   change.

## Opaque authenticated cursor format

The Beta 42 signed-plaintext form must not be emitted or accepted. A Beta 43
cursor must be one canonical, unpadded base64url token containing only:

- a freshly generated 96-bit nonce;
- AES-GCM ciphertext; and
- the authentication tag.

The existing process-local cursor secret must be domain-separated into a
256-bit encryption key. Canonical JSON claims must be encrypted with the
already pinned `cryptography==50.0.0` implementation and authenticated with
fixed, versioned associated data specific to plan-observability cursors. The
internal cursor-format version must be incremented. No plaintext claim,
metadata segment, key material, deterministic nonce, mutable cursor table,
filesystem state, new secret, or configuration option is permitted.

Two encodings of identical claims must produce different public tokens while
decoding internally to identical claims. Base64url-decoding a public token must
not expose parseable JSON or recognizable plan identity, plan hash, dependency
evidence fingerprint, full-set fingerprint, detail section, offset, page size,
ordering version, or cursor version. The largest valid token must remain below
the existing 2,048-character public input bound.

The process-local key lifecycle remains unchanged. A process restart may safely
invalidate an outstanding cursor.

## Binding and refusal behavior

Every cursor must retain authenticated bindings to:

- plan ID and immutable plan hash;
- dependency-evidence fingerprint;
- requested detail section;
- ordering version and cursor-format version;
- complete-collection fingerprint;
- page offset; and
- bounded page size.

Decoding must strictly validate canonical base64url, total size, nonce and tag
bounds, AES-GCM authentication, UTF-8 JSON, the exact claim-key set, claim
types, and numeric bounds. Malformed, legacy signed-plaintext, truncated,
extended, padded, noncanonical, authentication-failed, and unsupported tokens
must return the existing bounded `INVALID_CURSOR` result without cursor bytes,
claims, or keys in error output.

Preserve the existing refusal taxonomy after successful authenticated decode:

- a different page size is `INVALID_CURSOR` with `page_size_mismatch`;
- a different plan or detail section is `STALE_CURSOR`;
- changed persisted plan or evidence authority is `STALE_CURSOR`; and
- an out-of-range offset is `INVALID_CURSOR`.

Invalidation must never silently restart traversal. Mutating the nonce, the
beginning, middle, or end of the ciphertext, or the authentication tag must
fail authentication.

## Traversal, bounds, and negative reachability

Preserve deterministic ordering, full-set counts and fingerprints, adaptive
response bounding, summary-first projection, contract-v1 sanitization,
model-v3 visibility, lifecycle projection, and the 60,000-character response
boundary. Cursor bytes may vary; items, totals, ordering, and full-set
fingerprints from repeated unchanged traversals must remain deterministic.

An unchanged 100-obligation traversal and a multi-page downstream-profile
traversal must return every persisted record exactly once without duplicates
or omissions. Cursor encoding and decoding must derive only from the existing
persisted plan projection. They must perform no dependency refresh, provider
call, provider-health change, fallback, plan save, lifecycle mutation,
audit-authority creation, approval action, lock acquisition, dispatch, Home
Assistant access, or other write.

Responses and bounded errors must exclude raw configuration, credentials,
secrets, cursor keys, plaintext cursor claims, tokens other than the required
opaque continuation value, and unbounded diagnostic material. Existing
sanitization and fail-closed projection behavior remain authoritative.

## Required validation

Run the focused plan-observability, Beta 26 lifecycle, Beta v2 schema,
change-impact compatibility, and RC1 compatibility suites. Run the same broader
389-test governance, F2/F3, routing, and no-fallback matrix used for Beta 42.
Require the public schema fingerprints to remain unchanged.

Run metadata validation, exact Beta 43 staged-document authority, isolated
promotion-candidate validation, protected Fast Metadata and Validation, Full
and Evidence tiers, whitespace validation, and PR-evidence generation against
current `origin/main`. Declare both `governance/service.py` and
`.release/next-version` to the protected-path gate.

Record exact commands, counts, failures, skips, and environment limitations.
Windows CRLF historical-fixture failures must remain disclosed as failures;
they are not Linux or exact-head CI passes. Exact-head Linux CI must be
completely green before independent review.

## Compatibility, rollback, and non-actions

Stable v1.1.2, the public `get_change_plan` inputs and defaults, public output
fields, schema fingerprints, tool identity, registration, annotations, held
status, the 51-tool count, plan storage, provider routing and admission,
fallback policy, helper-risk model, approval and lock authority, execution
behavior, write authority, workflows, and deployment behavior remain
unchanged.

Before promotion or deployment, rollback is a coherent revert of the Beta 43
cursor implementation/tests commit and staging-document commit. Existing
persisted plans require no rewrite or migration. Any outstanding Beta 43
cursor becomes invalid after rollback, which is the existing safe cursor
invalidation behavior.

This acceptance procedure authorizes no Home Assistant access, live plan
inspection, plan creation, approval, apply, dispatch, promotion, tag, release,
publication, or deployment. Those activities require separate explicit
authorization.
