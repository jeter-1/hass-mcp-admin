# HA MCP Engineering Server 2.2.0-beta.16 acceptance

## Source boundary

- stacked base: F3-0 head `77d8f19b3dc12ec94eef134375ddcbd5baeb2670`
- Engineering version: `2.2.0-beta.16`
- stable version: `1.1.2`
- adapter contract: `f3-operation-adapter-v1`
- task schema: 1
- Engineering-local tools: 48
- exact protocol: `2025-03-26`
- secure pins: `aiohttp==3.14.3`, `cryptography==50.0.0`
- fallback: 0

No source or CI validation may access production Home Assistant, HAOS,
Supervisor, credentials, tokens, deployed MCP endpoints, or live provider
responses.

## Required F3-A evidence

1. Prove all lock operations use a stable cross-process transaction lock and
   that a multi-lock write either commits the complete set or leaves the prior
   state authoritative.
2. Prove deterministic bytewise acquisition, reverse release,
   shared/exclusive compatibility, duplicate evidence union, lease renewal,
   task/owner binding, fencing rejection, explicit stale recovery, corruption
   refusal, and restart persistence.
3. Prove a complete valid preflight and every required fenced lock precede the
   durable intent transaction.
4. Prove intent and `dispatch_count=1` commit before provider invocation, a
   persistence failure invokes the provider zero times, and every post-intent
   reconstruction is observation/readback only.
5. Exercise all 15 required failure boundaries. No attempt may exceed one
   adapter dispatch or one simulated mutation.
6. Prove active and terminal duplicates reuse the authoritative task and never
   create another dispatch attempt or competing lock set.
7. Prove cancellation is pre-intent only and manual review retains an explicit
   conflict hold.
8. Require bounded normalized outcomes, evidence, diagnostics, internal
   metrics, and events without raw provider content.

## Runtime-isolation acceptance

Require source tests proving that no module outside the isolated F3 package
imports or instantiates it, no existing adapter is migrated, no provider or
public registration changes, no Dashboard write becomes reachable, and no
central health field is added. Task and plan schemas must remain unchanged.

## Compatibility acceptance

Exact 7.14.2 must remain 78 advertised tools, 26 delegated reads, zero held,
48 local, and 74 total. Exact 8.0.0 must remain 78 advertised, 24 delegated,
exactly two held, 48 local, and 72 total. The held set must remain exactly
`ha_search` and `ha_get_operation_status`. Require zero missing, quarantine,
unreviewed, automatic-read mismatch, or fallback counts.

Dashboard v3 reads and all current operational providers must retain their
existing contracts without actual provider dispatch during compatibility
tests. Stable-v1 source must have an empty diff.

## Validation tiers

Require compilation, YAML, focused F3 suites, existing task/plan/lock and
provider regressions, Fast, Full, exact-head Evidence, fresh `pip check`,
strict `pip-audit` with no known vulnerabilities, stable and Engineering
packaging, amd64/arm64/arm-v7 no-push builds, exact-image 7.14.1/7.14.2/8.0.0,
immutable add-on runtime, disposable real-HA contracts, secret scan,
protected-path validation, whitespace, and PowerShell parsing.

The draft pull request must remain stacked on F3-0 while PR #87 is unmerged.
Nothing in this acceptance document authorizes publication, deployment,
tagging, merging, adapter migration, dashboard writing, or live access.
