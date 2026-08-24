# Engineering 2.2.0-beta.43 release notes

Beta 43 stages a narrow opacity correction for the continuation cursors added
to `get_change_plan` in Beta 42. Engineering 2.2.0-beta.42 remains advertised
until a separately authorized protected promotion. Stable v1.1.2 and the
51-tool Engineering registration remain unchanged.

This release changes cursor serialization only. It does not change plan
summaries, evidence extraction, helper-risk classification, policy, approval
authority, locking, dispatch, execution, provider behavior, or persisted plan
records.

## Authenticated-encryption cursor format

Beta 42 encoded cursors as signed canonical JSON followed by an HMAC signature.
That protected integrity but left recognizable plan and pagination metadata in
the decodable first segment.

Beta 43 replaces that format with one unpadded base64url token containing a
fresh 96-bit nonce and AES-GCM authenticated ciphertext. A domain-separated
256-bit encryption key is derived from the existing process-local cursor
secret, and fixed versioned associated data binds the ciphertext to the
plan-observability cursor domain. No new dependency, secret, configuration
option, server-side cursor table, or storage format is introduced.

The public token no longer exposes recognizable plan ID, plan hash, dependency
evidence fingerprint, full-set fingerprint, detail section, ordering version,
offset, or page size. Identical claims receive independent random nonces, so
their public tokens differ while their authenticated internal claims remain
the same. Tokens remain below the existing 2,048-character input bound.

## Strict refusal with existing error semantics

The decoder now enforces canonical unpadded base64url, bounded token and claim
sizes, the exact claim-key set, numeric bounds, AES-GCM authentication, and
canonical JSON after decryption. It rejects the Beta 42 plaintext format,
invalid characters, padding changes, truncation, appended bytes, noncanonical
encodings, unsupported versions, and any nonce, ciphertext, or tag mutation.

Malformed, legacy, and authentication-failed values continue to use the
bounded `INVALID_CURSOR` response. Existing post-decode behavior is preserved:
page-size mismatch and out-of-range offsets remain invalid; cross-plan,
cross-section, and changed persisted authority remain stale. Invalid cursors
never silently restart a traversal, and errors contain no cursor contents,
plaintext claims, or key material.

The process-local signing-secret lifecycle is unchanged. As before, a restart
may safely invalidate an outstanding cursor.

## Traversal and authorization boundaries

Deterministic item ordering, complete-collection fingerprints, summary-first
projection, adaptive response bounds, contract-v1 sanitization, model-v3
visibility, and lifecycle projection are unchanged. Repeated 100-obligation and
multi-page downstream-profile traversals return identical ordered records,
totals, and fingerprints without duplicates or omissions even though cursor
bytes themselves are randomized.

Cursor operations continue to read only existing persisted plan evidence. They
perform no dependency refresh, provider or Home Assistant access,
provider-health update, fallback, plan save, audit-authority creation, approval,
locking, dispatch, lifecycle mutation, or other write.

## Compatibility and non-actions

The public `get_change_plan` inputs, defaults, outputs, and schema fingerprints
are unchanged. Stable v1.1.2, public tool identity, MCP registration,
annotations, held status, the 51-tool count, provider routing and admission,
fallback policy, helper-risk and policy models, approval and execution
authority, persisted storage, workflows, Dockerfile, repository metadata, and
deployment behavior are unchanged.

This feature and staging change perform no Home Assistant access, plan
creation, live plan inspection, approval, application, dispatch, promotion,
tag, release, publication, or deployment. Before promotion, rollback is a
coherent revert of the Beta 43 implementation/tests and staging commits; no
data migration or live-state restoration is required.
