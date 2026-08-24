# Engineering 2.2.0-beta.42 acceptance

Beta 42 stages HAMCP-089.R3 bounded persisted-plan observability. Engineering
2.2.0-beta.41 remains the advertised version until a separately authorized
protected promotion. Stable v1.1.2 and the 51-tool Engineering registration
remain unchanged.

This candidate improves read-only inspection of persisted change plans. It does
not change dependency extraction, helper-risk classification, policy decisions,
approval authority, locking, dispatch, or execution behavior.

## Source and release authority

1. Resolve the feature base to current `origin/main` and require the reviewed R3
   implementation head and a clean worktree.
2. Require advertised Engineering `2.2.0-beta.41`, staged Engineering
   `2.2.0-beta.42`, stable `1.1.2`, and exact staged-document resolution to this
   document and the matching Beta 42 release notes.
3. Require `.release/next-version` to contain exactly `2.2.0-beta.42`. The
   advertised version authorities in `config.yaml`, `version.py`, and the
   metadata validator must remain Beta 41 in the feature pull request.
4. Require public tool accounting to remain 51, with no registration,
   annotation, held-status, routing, provider-admission, fallback, workflow,
   Dockerfile, `repository.yaml`, or deployment-metadata change.

## Backward-compatible read contract

`get_change_plan` retains required input `plan_id` and adds these optional,
backward-compatible inputs:

- `detail_section`, defaulting to `summary`, with supported values `summary`,
  `obligation_evidence`, and `downstream_profiles`;
- `cursor`, defaulting to the empty string and bounded to 2,048 characters;
- `page_size`, defaulting to 20 and bounded from 1 through 100.

Existing callers that provide only `plan_id` must receive a complete, valid,
bounded summary response rather than truncated JSON. A small plan remains easy
to inspect without requesting a detail page.

## Canonical bounded summary

Every successful plan read must expose the canonical summary before optional
large detail collections. Where persisted data applies, require the response to
retain:

- plan identity, lifecycle status, and plan hash;
- dependency-risk binding model and `helper_dependency_replan_required`;
- coverage completeness, evidence completeness, semantic precision, execution
  eligibility, and physical consequence;
- exact-dependency, opaque-obligation, proven-exclusion, proven-neutral, and
  coverage-failure counts plus all retained failure reason codes;
- retained, total, and overflow obligation counts and the full-set fingerprint;
- retained and total downstream-profile counts and the full-set fingerprint;
- relevant and consequential automation counts and the dependency-evidence
  fingerprint; and
- explicit truncation, detail-availability, and pagination indicators.

The summary must remain visible when no detail page is requested and when either
persisted collection would otherwise exceed the read-gateway response limit.
Historical model-v3 plans must expose their stored model and
`helper_dependency_replan_required=true`; their history must not be recomputed
under the current model.

## Plan-bound pagination

Require deterministic, bounded pagination for `obligation_evidence` and
`downstream_profiles`, with at most one requested detail collection in a
response. Each page must report the returned count, total count, continuation
state, ordering version, and full-set fingerprint.

Across an unchanged traversal, every persisted record must be returned exactly
once in stable order, without duplicates or omissions. Pagination must derive
only from the immutable persisted plan binding. It must not refresh the
dependency index or consult current Home Assistant state.

An opaque continuation cursor must be bound to the plan ID, immutable plan hash,
dependency-evidence fingerprint, requested detail section, deterministic
ordering version, full-set fingerprint, position, and bounded page size. Require
explicit refusal of:

- malformed or tampered cursors;
- cursors from another plan or detail section;
- cursors whose persisted plan or evidence authority has changed; and
- unsupported cursor or ordering versions.

Cursor invalidation must never silently restart traversal. Cursors must not
contain credentials, secrets, raw configuration, mutable server state, or
unbounded text.

## Bounds, sanitization, and negative reachability

Require every response to remain below the configured 60,000-character public
boundary with explicit safety headroom. Existing sanitization and redaction
must run before projection and pagination. Raw configurations, credentials,
secret-bearing fields, signing material, tokens, and unbounded diagnostic text
must remain absent from responses and cursors.

Plan inspection must perform no dependency refresh, current-state recomputation,
provider call, provider-health mutation, fallback, approval action, lock
acquisition, dispatch, audit-authority creation, plan mutation, or other write.
Expired, invalidated, model-v3, and model-v4 plans remain inspectable according
to their persisted authority while retaining their existing actionability rules.

Pagination and summary projection must have zero effect on helper-risk
classification, evidence fingerprints, policy, approval binding, lock
projection, execution eligibility, duplicate suppression, or provider routing.

## Required validation

Run the focused plan-observability, schema, and compatibility suites; the broader
governance, F2/F3, routing, and no-fallback matrix; metadata validation; exact
Beta 42 release-document validation; and isolated promotion-candidate
validation. Run protected Full and Evidence tiers with every changed protected
path declared, then generate PR evidence against current `origin/main`.

Require deterministic schema fingerprints, unchanged registration and tool
identity, valid exact-bound and above-bound responses, complete multi-page
traversal, cursor refusal coverage, secret exclusion, and negative reachability
for refresh, provider access, fallback, approval, locking, dispatch, and
mutation.

Record every command and exact test count. Windows CRLF historical-fixture
limitations must be reported as failures rather than treated as passing
evidence. Exact-head Linux CI must pass before independent review.

## Compatibility, rollback, and non-actions

Stable v1.1.2, the 51-tool count, public tool identity, registration, annotations,
held status, provider routing and admission, fallback policy, write authority,
storage formats, and deployment behavior remain unchanged. The public schema
change is limited to optional, backward-compatible read inputs on the existing
`get_change_plan` tool.

Before promotion or deployment, rollback is a coherent revert of the Beta 42
feature and staging commits. No storage migration, plan rewrite, or live-state
restoration is required because this feature reads existing persisted evidence
without changing it.

This acceptance procedure authorizes no Home Assistant access, live plan
inspection, approval, apply, dispatch, promotion, tag, release, publication, or
deployment. Those activities require separate explicit authorization.
