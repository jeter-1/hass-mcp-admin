# Engineering 2.2.0-beta.44 acceptance

Beta 44 corrects the post-release Beta 43 downstream-profile pagination defect.
Engineering 2.2.0-beta.43 remains advertised until a separately authorized
protected promotion. Stable v1.1.2 and the 51-tool Engineering registration
remain unchanged.

## Defect and selected model

Beta 43 paged only whole `downstream_profiles` records. When the next valid
record did not fit the remaining 52,000-character internal response budget, it
was removed from the page. A subsequent empty page failed with
`detail_record_exceeds_response_budget`, so no cursor could advance beyond that
record. The released regression checked only the first adaptive page.

Beta 44 preserves whole-record pages for profiles that fit. When one profile
cannot fit on an otherwise empty page, `get_change_plan` returns one bounded
fragment in `detail.fragments`. The fragment is a slice of the complete
profile's canonical, key-sorted, compact JSON UTF-8 bytes. Its payload is
canonical unpadded base64url. No profile field is dropped, replaced, or hashed
instead of being returned.

The detail projection distinguishes:

- `returned_count`: complete whole records in `detail.items`;
- `returned_fragment_count`: fragments in this response; and
- `completed_logical_record_count`: logical records completed by whole items or
  by a final fragment in this response.

Fragment metadata identifies the logical-record index and fingerprint,
persisted `profile_fingerprint`, fragment index, half-open byte range, total
canonical byte count, final-fragment state, payload encoding, and a deterministic
fingerprint of the complete fragment projection. A client reconstructs one
logical profile by validating the fragment fingerprint, decoding each payload,
concatenating contiguous byte ranges for the same logical-record fingerprint,
and parsing JSON only after `is_final=true`. A fragment page contains no whole
record; after its final fragment the next cursor advances to the next logical
record.

## Cursor authority and traversal

The internal cursor format is version 3 and ordering version 2. Beta 44 uses a
new domain-separated AES-GCM key context and associated-data version. An
outstanding Beta 43 cursor must fail closed after the Beta 44 process restart
and must never be interpreted using Beta 44 semantics.

In addition to Beta 43's plan, evidence, section, full-set, ordering, logical
offset, and page-size claims, the opaque cursor authenticates the exact logical
record fingerprint, intra-record byte offset, and fragment index. All claims
remain encrypted behind a fresh 96-bit nonce, AES-GCM ciphertext and tag, fixed
associated data, canonical unpadded base64url, and the 2,048-character bound.
No identifier or position is present in plaintext.

For every unchanged traversal require deterministic logical order, total count,
full-set fingerprint, profile contents, and fragment byte ranges. The deployed
31-profile shape must pass the formerly blocking ninth profile and return all 31
logical profiles exactly once. Also require oversized records in the first,
middle and final positions, consecutive oversized records, page sizes 1, 20 and
100, and two identical complete traversals. Every Engineering result, including
outer structured-tool framing, must remain below 60,000 characters.

Reject cursor tampering, truncation, extension, padding, noncanonical encoding,
cross-plan or cross-section use, page-size mismatch, changed plan/evidence/
collection/record material, invalid logical or byte offsets, completion cursors,
and process-restart replay. Refusal is bounded and never restarts or skips
evidence.

## Read-only and compatibility requirements

Summary-only reads, obligation pagination, adaptive multi-record pages, logical
profile ordering, total-count and full-set-fingerprint semantics, contract-v1
through current persisted-plan projection, sanitization, lifecycle projection,
and raw-configuration exclusion remain unchanged. Beta 43 persisted plans need
no migration. The public input schema and the 1-through-100 `page_size` bound do
not change; the fragment fields are an additive output extension used only when
a complete logical record cannot fit.

Successful, interrupted and refused traversals must leave persisted files
byte-identical and perform zero dependency refreshes, Home Assistant reads,
provider calls, provider-health changes, fallback, plan writes, migrations,
approval actions, locks, audit-authority creation, dispatches or lifecycle
mutations.

## Validation and release boundaries

Require released-head falsification with production action-profile fields,
focused complete-traversal and cursor tests, HAMCP-089 plan observability, Beta
26 lifecycle, Beta v2 schema, change-impact and RC1 compatibility, Beta 37-43
governance/provider/no-fallback regressions, full unit discovery, protected Fast,
Full and Evidence gates, compilation, YAML and PowerShell checks, dependency and
strict vulnerability validation, secrets, whitespace, stable-v1 comparison,
isolated Beta 44 promotion-candidate validation, and exact-head CI.

Post-deployment acceptance is separate and read-only: confirm exact Beta 44,
traverse the deployed 31-profile collection twice, and verify all logical
records, response bounds, stable ordering/count/fingerprint, and zero reads,
writes, locks, approvals, dispatches or fallback. The governed helper write
canary remains separately authorized after this read-only gate.

This feature task authorizes no merge, promotion, image, tag, publication,
deployment, restart, live Home Assistant access, plan creation, approval, apply,
dispatch or household canary.
