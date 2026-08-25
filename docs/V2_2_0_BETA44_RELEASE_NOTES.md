# Engineering 2.2.0-beta.44 release notes

Beta 44 stages a bounded completeness correction for the persisted
`downstream_profiles` pages returned by `get_change_plan`. Engineering
2.2.0-beta.43 remains advertised until protected promotion. Stable v1.1.2 and
the 51-tool Engineering registration remain unchanged.

## Complete oversized-profile traversal

Beta 43 returned only whole profile records. A valid record larger than the
remaining detail-page budget could become permanently unreachable: the prior
page stopped before it, and the empty continuation failed rather than advancing.

Profiles that fit remain ordinary whole objects in `detail.items`. Beta 44 adds
an explicit continuation representation only when a profile cannot fit on an
empty page. `detail.fragments` then carries bounded slices of the profile's
complete canonical JSON UTF-8 bytes using canonical unpadded base64url. The
metadata exposes deterministic logical-record identity, byte range, total byte
count, final-fragment state and fragment fingerprint. Separate whole-record,
fragment and completed-logical-record counts prevent partial data from being
mistaken for a complete profile.

Clients reconstruct a profile by checking each fragment fingerprint, decoding
and concatenating contiguous byte ranges for one logical-record fingerprint,
and parsing JSON after the final fragment. No persisted field is omitted or
substituted with a digest. Repeated unchanged traversals preserve logical order,
total count, full-set fingerprint and complete reconstructed profiles without
duplicates or omissions.

## Opaque continuation binding

The encrypted cursor and ordering formats advance to versions 3 and 2. The
AES-GCM claims now bind the exact record fingerprint, intra-record byte offset
and fragment index in addition to the existing plan, evidence, section,
collection, logical offset, page-size and version authorities. A new
domain-separated key context and associated-data version makes outstanding Beta
43 cursors fail closed after restart.

Fresh 96-bit nonces, ciphertext and tag, canonical unpadded base64url, the
2,048-character bound and bounded errors remain unchanged. Tampered, malformed,
stale, cross-scope, out-of-range and noncanonical continuations never restart or
skip traversal.

## Bounds, compatibility and non-actions

Every structured MCP response remains below 60,000 characters, including tool
framing. The public inputs, `page_size` range, summary fields, whole-record
behavior, obligation pages, persisted-plan contracts, sanitization, full-set
fingerprint semantics, approval, locking, dispatch, provider routing and zero
fallback remain unchanged. The fragment fields are additive; nonfragmented
pages report an empty fragment collection, while payload metadata is present
only for otherwise unreachable downstream profiles. Beta 43 persisted plans
require no migration.

Pagination continues to read persisted evidence only. It performs no dependency
refresh, Home Assistant or provider read, provider-health change, fallback,
write, migration, approval, audit-authority creation, lock, dispatch or lifecycle
mutation.

This staging change performs no merge, promotion, publication, deployment,
restart, live Home Assistant access or governed helper canary. Post-deployment
read-only traversal and the helper write canary require separate authorization.
