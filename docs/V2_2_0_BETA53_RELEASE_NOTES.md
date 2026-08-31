# Engineering 2.2.0-beta.53 release notes

Beta 53 is materialized with canonical entity-registry deduplication and
bounded selector-authority diagnostics. Engineering now advertises
2.2.0-beta.53 after the repository's protected promotion mechanism consumed
`.release/next-version`. Stable v1.1.2 and all provider, routing, fallback,
workflow, container and deployment-authority boundaries are unchanged. This
source promotion does not publish an image, merge the draft change, or deploy
the release.

## Canonical-identical registry records

Home Assistant's supported entity registry is keyed by `entity_id`, and its
WebSocket list is constructed from the mapping's values. Beta 53 therefore
treats multiple bounded records for the same valid entity ID as one semantic
record only when their complete strict canonical JSON bytes are identical.
Mapping-key order is normalized; list order, scalar type and every field remain
material. No trimming, case folding, coercion, repair, field merge or label
union is performed. Canonicalization enforces byte, node and depth budgets
before serialization, so an oversized or wide record remains malformed
evidence rather than consuming unbounded work.

Canonical retention is also capped at 16,777,216 aggregate bytes in each
provider pass. Exhaustion retains no additional canonical blobs and makes the
selector evidence incomplete with an exact diagnostic reason. The raw
record-count bound remains authoritative and runs before deduplication;
oversized-response ordering uses fixed-size canonical digests.

Raw response bounds run first. Conflicting records, unreadable labels,
relevant malformed or unsupported values, non-finite numbers, incomplete
inventories and every overflow remain fail-closed. An unrelated readable
conflict remains selector-local unless its label collection is unreadable.

The sanitized Beta 52 replay continues to reproduce the exact 24-obligation,
two-profile historical failure when exact Beta 52 receives the proven
identical-duplicate input class. On Beta 53, that input has the same semantic
membership, target outcome, risk, actionability, approval/preflight evidence
fingerprint and F3 locks as the single-record control. This does not establish
that the input historically occurred for plan c990.

Fixture provenance now binds the shareable capture/report hashes, the
report-attested private source-capture hash without accessing that artifact,
the recorded sanitization operations, and the exact offline replay command and
runner hash. Exact Beta 52 must reproduce the complete normalized obligation
and downstream-profile row multisets—not only aggregate counts—before the
historical falsification control passes. The intentionally private raw capture
and raw-to-sanitized executable are not repository inputs, so the release does
not claim raw capture regeneration.

## Persisted selector diagnostics

Fresh `helper-dependency-risk-v12` plans persist bounded hash-safe diagnostics
for each retained literal label selector: lookup mode, resolved identity hash,
failure codes, membership and candidate counts/fingerprints, target
disposition, inventory completeness, raw/canonical counts, duplicate/conflict/
malformed counts, raw-bound state, and snapshot identity. Raw registry records,
unrelated entity IDs, endpoints, credentials and configurations are excluded.

The existing change-plan summary detail envelope paginates those diagnostics
without a new tool or new input field. Historical plans without diagnostics
retain the prior summary path. Persisted v3-v11 plans remain readable and
hash-stable but require replanning and cannot authorize current execution.

Exact dependencies and safety-critical downstream profiles remain
proportionally locked. Accepted identical multiplicity is diagnostic only.
Conflicts, malformed evidence, drift and incomplete authority remain
non-actionable, retain the conservative guard where required, and cannot reach
provider dispatch.
