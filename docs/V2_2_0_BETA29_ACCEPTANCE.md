# HA MCP Engineering Server 2.2.0-beta.29 Acceptance

## Authority and prerequisites

This document governs post-deployment validation only after Beta 29 has been
reviewed, merged, promoted, and separately authorized for deployment. **No
deployment is authorized** by this document or its presence in the repository.

Before any write canary, confirm the deployed Engineering build identity, exact
ha-mcp compatibility entry, Home Assistant compatibility lane, provider health,
tool counts, zero fallback, and an empty approval/task baseline for the canary.
Use a disposable, non-operational dashboard target and preserve its exact
pre-canary configuration and hashes for restoration and readback.

## Required sequence

### 1. Hyphenated existing-dashboard canary on ha-mcp 8.1.1

Create a fresh governed plan for an existing dashboard whose `url_path`
contains a hyphen. Approve it through the external approval surface, apply it
once, and require all of the following:

- exactly one `ha_config_set_dashboard` invocation;
- no direct Home Assistant route and no fallback;
- `provider_response_received: true` for a successful provider result;
- exact authoritative readback and `succeeded_verified`;
- a duplicate apply resolves idempotently without another dispatch.

This proves the general governed write machinery independently of the known
hyphenless-path incompatibility.

### 2. Existing `map` compatibility canary on ha-mcp 8.1.1

Attempt to create a fresh plan for the existing hyphenless `map` dashboard.
Require plan creation to fail with
`dashboard_write_existing_hyphenless_path_incompatible` before approval,
best-practices retrieval, or provider dispatch. Confirm zero setter calls and
no dashboard mutation.

### 3. Fresh `map` plan after an upstream correction is admitted

Do not run this step until the upstream correction is published, reviewed,
entered into compatibility policy, and selected with exact admission. After
that separate work, create a fresh `map` plan, approve it externally, apply it
once, and require exactly one setter invocation, exact authoritative readback,
and `succeeded_verified`.

### 4. Deliberate stale-state canary

Only after steps 1 through 3 pass, create a disposable dashboard plan, alter
the target independently before apply, and require a pre-dispatch stale-state
rejection with zero setter invocations for that plan.

## Failure-result acceptance

The synthetic regression corresponding to the Beta 28 live failure must show
that a structured ha-mcp validation rejection is recorded as a received
provider response. If the authoritative reread proves the original state is
unchanged, the execution must end `failed_post_dispatch` with
`provider_rejection_confirmed_no_change`, not `response_received: false` or
`verification_mismatch`. No retry or redispatch is permitted.

Stop after any failed step. Do not reuse a consumed approval or a prior plan,
and do not proceed to the stale-state canary until the preceding compatibility
questions have been resolved.
