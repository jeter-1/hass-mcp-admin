# Engineering 2.2.0-beta.57 acceptance

Beta 57 is the materialized source candidate for restoring the existing
governed dashboard-update capability against exact ha-mcp 8.4.1. It is based
on protected main `4b5bb8cbb91cf929fd7ff45d541c68ce4e616098`. The advertised
Engineering release is 2.2.0-beta.57, the staged declaration has been consumed,
and stable remains 1.1.2. Materialization does not publish or deploy the
release.

This document authorizes source review only. Merge, publication, deployment,
live Home Assistant access, and the reversible dashboard canary remain separate
owner decisions.

## Baseline falsification

Exact unmodified Beta 56 source was exercised through production-equivalent
fixtures before correction. With reviewed 8.2.0 dashboard evidence, inventory
and configuration reads succeeded but `create_dashboard_update_plan` failed
with `operational_provider_unavailable` and
`dashboard_provider_identity_unavailable`: planning requested the unrelated
add-on lifecycle identity rather than dashboard provider authority. With exact
8.4.1 evidence, the general 25-read profile remained admitted while dashboard
authority was held before any getter or setter call.

The new exact 8.4.1 provider and planning assertions failed on that source.
The available shipped history does not prove that dashboard planning ever
succeeded through the production binding, so this is recorded as an
implemented-capability defect rather than an asserted regression.

## Exact 8.4.1 dashboard authority

The candidate retains Beta 56's exact `homeassistant-ai/ha-mcp` 8.4.1 source,
tree, image, 78-tool catalog, and protocol authority. It adds a generated
binary-owned dashboard attestation for the exact getter and setter descriptors.
Dashboard authority binds:

- entry `ha-mcp-v8.4.1-7823b365`;
- source commit `701a7c26ac0e2309c7883a627d31873ab1510077`;
- image index
  `sha256:7823b36587a6e62efed271b26f3f72380b49f47364e5385580584e7ab2c60722`;
- protocol `2025-03-26`;
- exact catalog fingerprint
  `4303ead3f32c46658530a422ae37eec0d34d3f2e494a2122a7011593a568bf59`;
- exact getter and setter contracts, compiled constraints, provider generation,
  target storage identity, and baseline configuration fingerprints.

Getter-only evidence remains readable but cannot create an actionable plan.
Setter-only, incomplete, mismatched, expired, revoked, or malformed evidence
fails before provider dispatch. The setter is not registered or generically
forwarded. Dashboard creation, deletion, resources, metadata administration,
YAML dashboards, screenshots, direct-HA substitution, and fallback remain
unavailable.

## Planning, approval, and execution

The existing typed patch format now supports bounded RFC 6902 array `add`:
final `-` append and canonical numeric insertion from zero through the current
array length. Every declared change is represented by a complete bounded
before/after approval projection, rendered as inert escaped JSON and bound to
the preread, patch, result, and plan hashes. Protected, incomplete, tampered,
or oversized review evidence fails during planning or before approval.

Fresh exact f2-v2 dashboard plans require the single hash-, principal-, CSRF-,
and expiry-bound owner plan approval. Severe or uncertain frontend consequence
evidence remains elevated and visible but does not create a duplicate
severity-only acknowledgement. Historical f2-v1 approval bundles are not
reinterpreted.

Planning performs zero writes. Apply reacquires the exact dashboard, core, and
provider locks; revalidates provider authority, target, storage mode, baseline,
plan, and approval; persists durable intent; permits one setter call; and
requires authoritative exact reread for `succeeded_verified`. Sequential and
concurrent duplicates cannot create a second dispatch. Post-intent response
loss is recovered by reread and never by blind redispatch.

## PR #132 reconciliation

- Already present: protected-data refusal, stale-baseline refusal, durable
  one-dispatch ownership, duplicate suppression, exact reread, and recovery.
- Ported narrowly: final-array append, canonical numeric array insertion, and a
  complete hash-bound approval projection with inert HTML/JSON rendering.
- Reimplemented against current authority: planning-time projection bounds and
  diagnostics are integrated with contract-v3 governance and the canonical
  dashboard provider identity.
- Rejected as obsolete: the old branch's broad code structure, release context,
  and semantic-leaf limit expansion. The current 16-leaf, 16-operation, value,
  patch, growth, and result bounds remain authoritative.

## Required validation

Acceptance requires focused dashboard admission, identity, planner, compiler,
approval, F3, recovery, and historical compatibility tests; exact ha-mcp
8.0.0 through 8.4.1 and disposable Home Assistant lanes; complete unittest
discovery; Fast, protected Full, and clean-head Evidence; materialized-release
validation with `--require-materialized`; exact-image/App, packaging, and
architecture checks; compilation and structured-file checks; dependency and
strict vulnerability audits; secrets and whitespace checks; stable-v1
comparison; and exact tool, task-schema, approval-authority, routing, and
fallback assertions.

The exact 8.4.1 client catalog remains 51 Engineering tools plus 25 delegated
reads, for 76 total. Task schema remains 1, approval authority remains 3,
`ha_get_operation_status` remains held, and fallback remains zero. A sandbox
loopback denial or unavailable external lane is not a pass.

The source pull request must remain draft, unmerged, unpublished, undeployed,
and free of live-system access pending exact-head CI, one fresh manual
independent review, and separate owner authorization.
