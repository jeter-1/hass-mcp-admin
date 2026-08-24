# Engineering 2.2.0-beta.42 release notes

Beta 42 stages HAMCP-089.R3 bounded observability for persisted change plans.
Engineering 2.2.0-beta.41 remains advertised until a separately authorized
protected promotion. This change improves inspection only: it does not change
dependency extraction, helper-risk classification, policy, approval authority,
locking, dispatch, or execution behavior.

## Bounded summary-first plan reads

The existing `get_change_plan` tool now returns a canonical bounded summary
before any large evidence collection. The summary preserves plan identity and
status, plan and evidence fingerprints, dependency binding model, model-v3
replan requirements, coverage and evidence status, semantic precision,
execution eligibility, physical consequence, obligation outcomes, coverage
failure reasons, collection totals, overflow counts, automation counts, and
pagination availability when those values exist in the persisted plan.

Existing callers may continue to supply only `plan_id`. Three optional,
backward-compatible inputs provide bounded detail access:

- `detail_section` selects `summary`, `obligation_evidence`, or
  `downstream_profiles` and defaults to `summary`;
- `cursor` carries a bounded opaque continuation token and defaults to empty;
- `page_size` selects 1 through 100 records and defaults to 20.

Small plans remain directly readable. Large plans no longer lose their
canonical model, replan, completeness, failure, count, or fingerprint fields
merely because detailed evidence exceeds the public response boundary.

## Deterministic persisted-detail traversal

Obligation evidence and downstream automation profiles can be traversed as
separate deterministic pages. Each response includes returned and total counts,
continuation state, ordering version, and a fingerprint for the complete
persisted collection. An unchanged traversal returns each persisted record
exactly once without duplicates or omissions.

Opaque cursors bind the plan ID, immutable plan hash, dependency-evidence
fingerprint, detail section, ordering version, full-set fingerprint, position,
and page size. Malformed, tampered, cross-plan, cross-section, stale-authority,
and unsupported-version cursors are rejected. Invalid cursors never silently
restart pagination.

Pages are derived solely from the stored plan binding. Historical evidence is
not refreshed or reconstructed from the current dependency index. Model-v3
plans remain readable with `helper_dependency_replan_required=true`, while
model-v4 plans expose their exact persisted counts, failure reasons,
fingerprints, and available detail.

## Bounds and security

Responses retain safe headroom beneath the 60,000-character read-gateway limit.
Sanitization and redaction precede public projection and pagination. Raw
configuration, credentials, secret-bearing fields, tokens, signing material,
unbounded text, and mutable server state are excluded from responses and
cursors.

Plan inspection performs no dependency refresh, Home Assistant state read,
provider access, provider-health update, fallback, approval action, lock
acquisition, dispatch, audit-authority creation, persisted-plan mutation, or
other write. Pagination and summary construction have no authorization effect.

## Compatibility and non-actions

Stable v1.1.2, public tool identity, MCP registration, annotations, held status,
the registered tool count of 51, provider routing and admission, fallback
behavior, write authority, storage compatibility, workflows, Dockerfile,
repository metadata, and deployment behavior are unchanged.

Beta 42 adds no tool, route, provider, service call, fallback, approval path,
lock authority, or execution capability. It does not remediate or alter the
helper-risk classifications stored in existing plans; it makes the persisted
evidence needed for later diagnosis truthfully and safely inspectable.

This feature and staging change perform no Home Assistant access, live plan
inspection, plan approval or application, dispatch, promotion, release,
publication, or deployment. Before promotion, rollback is a coherent revert of
the Beta 42 implementation and staging commits; no data migration or live-state
restoration is required.
