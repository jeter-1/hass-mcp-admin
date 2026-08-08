# HA MCP Engineering 2.2.0-beta.27 configuration-metadata acceptance

## Release boundary

- baseline: `3999c393a708e45f17852aae26f01f3270413329` on current
  `origin/main` when the corrective branch was created;
- published Engineering version: `2.2.0-beta.26`;
- staged version: `2.2.0-beta.27`;
- upstream: exact reviewed `ha-mcp` 8.1.1;
- Home Assistant lanes: 2026.8 contract and exact-image validation;
- stable version: `1.1.2`; and
- merge, publication, deployment, live retry, and live Home Assistant access
  are excluded.

## Source and deterministic acceptance

Require all of the following before merge:

- reproduce the Beta 26 behavior with a category-enriched automation update:
  unknown metadata appears in the REST body and a received rejection is
  represented as post-dispatch uncertainty;
- accept a bounded automation or script `category` only as read-only registry
  metadata and surface that fact in plan warnings;
- omit `id` and `category` from automation/script REST provider descriptors and
  actual dispatch bodies while leaving the raw read evidence bounded and
  integrity protected;
- exclude `category` from behavioral diffs, fingerprints, stale-state checks,
  and verification so a category-enriched update can apply and verify without
  changing registry metadata;
- reject a category on create and every unknown automation top-level field
  before evidence reads, approval, durable intent, or provider dispatch;
- report an exact-endpoint Home Assistant 4xx as a received provider rejection,
  `dispatch_failed_confirmed`, and zero provider mutations;
- preserve timeout, connection loss, 5xx, malformed-response, response-loss,
  recovery, and no-redispatch semantics;
- advance normalization contracts and fail closed rather than reauthorize an
  outstanding plan under changed semantics;
- keep plan history readable and retain all approval, policy, F3, audit,
  projection, locking, recovery, and tamper checks;
- keep exact 8.1.1 accounting, `ha_search`, held
  `ha_get_operation_status`, Dashboard, zero fallback, public schemas, and
  stable v1.1.2 unchanged; and
- pass focused configuration/governance/F3 tests, Full, Evidence, exact-image,
  and both Home Assistant compatibility lanes.

## Later deployment acceptance

After separate merge, promotion, publication, and deployment authorization,
verify exact Beta 27 build provenance and confirm normal health, provider,
catalog, audit, governance, task/recovery, and fallback state. Do not reuse the
failed Beta 26 plan; create a fresh plan from a fresh authoritative automation
read.

Use a bounded category-enriched update equivalent to the door-clear
notification change. Confirm the approval review labels `category` as
read-only metadata and does not present it as a behavioral change. Approve and
apply once, then read back the exact automation and verify the requested action
exists, the entity-registry category is unchanged, the operation is
`succeeded_verified`, and no retry or fallback occurred.

Separately exercise an intentionally invalid top-level field before approval
and a controlled provider-rejection fixture where available. Hold if metadata
reaches the REST body, a rejection is called a verification mismatch, category
changes silently, verification ignores a behavioral difference, any older
plan gains authority, or public/provider/catalog accounting changes.
