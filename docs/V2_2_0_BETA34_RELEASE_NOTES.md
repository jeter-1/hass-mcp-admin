# Engineering 2.2.0-beta.34 release notes

Beta 34 stages two independently reviewed corrective workstreams from the
published Beta 33 source. Beta 33 remains the advertised Engineering release
until a separate protected promotion publishes Beta 34. This source change
does not deploy or access Home Assistant.

## A — Exact automation verification correction

Beta 33 successfully wrote the reviewed garage automation guard, but F3 then
reported `verification_mismatch`. The provider write did not fail. The false
mismatch came from applying the 200-character action-name limit to the
automation's `wait_template` during authoritative readback normalization.

Beta 34 gives `wait_template` source its own fixed, fail-closed 60,000-character
limit. The complete template remains byte-for-byte significant to normalized
semantic and hash verification. Empty, malformed, oversized, or behaviorally
different templates still fail closed. No approximate, lossy, or
provider-response-only verification was introduced.

## B — Projection-only historical policy compatibility

Two valid persisted records from the Beta 32 policy transition were being
reported as current-policy projection mismatches. Exact source-writer fixtures,
immutable source commits, stored subject and decision hashes, approval-bundle
integrity, terminal lifecycle, and deterministic regeneration establish the
two narrowly compatible historical profiles.

Compatibility is read/projection only. Persisted records are not migrated or
rewritten, and current policy remains mandatory for every authority-bearing
operation. A compatible historical record cannot be approved, consumed,
applied, recovered, reconciled into authority, rolled back, dispatched, or
redispatched. Its reported authorization effect is
`none_projection_only`.

The integration hardens that boundary locally. A non-applied historical
profile carrying `applied_at`, successful verification, rollback requests, an
apply request, or another applied execution outcome no longer matches at all.
A test-only structural guard also restricts direct use of the projection
validator to the three reviewed read/index helpers and excludes authority-path
families.

One source-reviewed Beta 6 container shares the structural Beta 32 prohibited
profile. It remains identified by its persisted contract/lifecycle variant;
Beta 34 does not rename or broaden the compatibility profiles.

## Compatibility and security boundaries

- Public MCP tools and schemas, task and plan schemas, approval authority,
  provider routes, dispatch behavior, and zero-fallback policy are unchanged.
- Exact ha-mcp 8.0.0, 8.1.0, 8.1.1, and 8.2.0 lanes remain required.
- Disposable Home Assistant coverage preserves 2026.7.2 and 2026.8.0 and adds
  exact 2026.8.1 by immutable OCI digest.
- Stable v1.1.2 is unchanged.
- No production record, credential, signing material, or private discovery
  evidence is included.
- Credential rotation prompted by private operational evidence remains a
  separate operator-controlled follow-up.
