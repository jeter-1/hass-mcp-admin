# HA MCP Engineering Server 2.2.0-beta.12 acceptance

## Source boundary

- required base: `229e3dc85fc587f53674b093f82bdf3c5beb7011`
- Engineering version: `2.2.0-beta.12`
- stable source version: `1.1.2` (unchanged and operationally retired)
- Engineering-local tools: 48
- task schema: 1
- approval authority: 3
- protocol: `2025-03-26`
- fallback: 0

Record the exact reviewed PR head and later merge/build identity separately.
Source validation must not access production Home Assistant, Engineering,
HAOS or secrets.

## Required source validation

1. Validate the protected evidence manifest, exact release registry,
   deterministic regeneration, compilation, YAML, add-on metadata,
   dependencies, secret patterns, protected paths and whitespace.
2. Run the Beta 12 reproduction/diagnostic suite, upstream registry and
   read-gateway suites, dashboard constraints, metadata, historical plans,
   F1 durable tasks, F2 authority-v3 governance and the complete unit suite.
3. Require exact 7.14.2 accounting: 78 advertised, 26 exact reads, 74 total,
   zero missing, unreviewed, mismatch, quarantine and fallback.
4. Require exact add-on-mode 8.0.0 accounting: 78 advertised, 24 exact reads,
   two held, 72 total, zero missing, unreviewed, unexplained quarantine and
   fallback. The held set must be exactly `ha_search` and
   `ha_get_operation_status`, neither registered nor callable.
5. Require 8.0.1 and 8.1.0 to fail exact release selection with 48 local tools
   only, no semver inheritance and no fallback. The protocol allowlist must
   remain exactly `2025-03-26`.
6. Deliberately change a runtime-only reviewed field and require one bounded
   `runtime_contract_mismatch` with equal ordinary hashes, unequal runtime
   hashes and the exact changed field. Require invalid policy field presence,
   types, deployment, bounds and extra fields to quarantine.
7. Require a clean worktree, unchanged stable source and no provider, write,
   workflow, protocol, dashboard, backup, lifecycle or fallback expansion.

## Immutable artifact evidence

The exact standalone amd64 artifact must reproduce 78 tools, operational
fingerprint `0bc81aa7bd94416385520b9c4c4f7d9ccbc6a49f8f65b8a2a599135463327316`
and strict wire-order fingerprint
`ff18cda3ca27abc8cca69685fb5240942cbe24a1508f73b9a26e57e1afe44d5a`.

The exact add-on amd64 artifact must reproduce 78 tools, operational
fingerprint `c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768`
and strict wire-order fingerprint
`f061e48a5d049a2fe84f8b46451a8c2928e0eb5fc68181cf0cbbe71ae5025727`.
The observed field diff must be limited to policy `deployment`, `enabled` and
`live` for all four representative reads, with `rules=0` unchanged in both
artifacts. Admission must nevertheless validate and normalize all four dynamic
policy-state values: `deployment`, `enabled`, `live` and `rules`. Unavailable
native architecture lanes must be recorded explicitly; source-payload
inspection is not reported as native execution.

## Preserved provider and governance boundaries

Dashboard remains reviewed list inventory and exact get by canonical
`url_path`, with no fuzzy selection, `view_path`, screenshots, preference
writes or mutation. Backup remains bounded full-backup creation without
restore, delete or download. Lifecycle retains controlled reload, exact add-on
restart and Home Assistant restart with existing approval, identity, readiness
and no-blind-redispatch constraints. Historical plans remain readable,
authority version remains 3, task schema remains 1, and no production state is
modified by source validation.

## Later production canary — separately authorized

After an approved merge, publication and deployment, confirm exact Engineering
version/build/clean identity, upstream 8.0.0 and protocol, the live
`c61b0959…` catalog, 24/2/72 accounting, held-tool non-registration, zero
mismatch/quarantine/fallback, representative `ha_get_state` success, dashboard
constraints, bounded diagnostic health and rollback readiness. Stop on any
unknown version, changed descriptor outside the reviewed policy state, held
tool exposure, write reachability, fallback, protocol change, missing artifact
identity or governance/provider regression.

The source PR remains draft and unmerged. This document does not authorize or
claim deployment, canary execution or production acceptance.
