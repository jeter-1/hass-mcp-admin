# HA MCP Engineering Server 2.2.0-beta.19 acceptance

## Source boundary

- stacked base: F3-A head `9f51830907799d4a409bf230c11fe8fbe8c61ead`
- F3-0 dependency: `77d8f19b3dc12ec94eef134375ddcbd5baeb2670`
- Engineering version: `2.2.0-beta.19`
- stable version: `1.1.2`
- adapter contract: `f3-operation-adapter-v1`
- task schema: 1
- Engineering-local tools: 48
- exact protocol: `2025-03-26`
- secure pins: `aiohttp==3.14.3`, `cryptography==50.0.0`
- fallback: 0

No source or CI validation may access production Home Assistant, HAOS,
Supervisor, credentials, tokens, deployed MCP endpoints, or live providers.

## Required operational conformance evidence

1. Preserve approved Beta 15 plan target, operation, policy, risk, effect,
   provider admission, baseline, exact arguments, verification, warning,
   limitation, and rollback projections for all four operations.
2. Reject unknown capability, operation, target, argument, release, protocol,
   lifecycle response, fallback, and unsupported operational action before
   intent with zero provider dispatches and effects.
3. Prove canonical complete resource/provider lock sets use only F3-A atomic
   acquisition, ownership, fencing, durable generation, reverse release, and
   reconstruction behavior.
4. Prove exact authoritative preflight after locks, including fresh backup,
   configuration, service, add-on identity, HA/runtime/storage, and admission
   reads appropriate to each operation.
5. Prove F3-A durable intent commits before the only provider call, intent
   failure calls the provider zero times, and every possibly dispatched
   reconstruction is observation/verification only.
6. Exercise response loss before and after effects plus required process-loss
   boundaries, duplicate apply, pre-intent cancellation, post-intent refusal,
   evidence deadline, manual review, and no blind redispatch.
7. Prove backup exact readback, reload post-readiness, add-on restart evidence
   beyond an old running state, and Beta 11 bounded HA restart reconciliation.
8. Prove bounded metrics/events never expose provider responses, inventory,
   metadata, URLs, secrets, tokens, or raw exception strings.

Every attempt must have at most one adapter dispatch, one provider mutation,
and one synthetic effect. Every pre-dispatch rejection must have all three at
zero.

## Runtime-isolation acceptance

Require source tests proving application startup and current governance,
provider, and reconciliation modules do not import or instantiate F3-C2. The
four planning routes and operational apply route must remain unchanged. No
public tool, health field, plan/task schema, provider route, automatic-read
entry, configuration route, Dashboard read, or stable-v1 source may change.

Activation is blocked until F3-D supplies accepted production task/evidence
binding and selective bounded manual-review conflict holds. The draft must
remain stacked on F3-A, dependent on PR #87, and unmerged until accepted Beta
17 and Beta 18 delivery unless the operator changes the sequence.

## Compatibility acceptance

Exact 7.14.2 remains 78 advertised tools, 26 delegated, zero held, 48 local,
and 74 total. Exact 8.0.0 remains 78 advertised, 24 delegated, exactly two
held, 48 local, and 72 total. The held set stays exactly `ha_search` and
`ha_get_operation_status`. Require zero missing, quarantine, unreviewed,
automatic-read mismatch, or fallback counts.

Exact 7.14.2 legacy lifecycle decoding and exact 8.0.0 structured add-on detail,
including the 71,986-byte live-equivalent fixture, must pass without broadening
unknown-release structured acceptance or the generic text bound.

## Validation tiers

Require compilation, YAML, F3-0/F3-A and F3-C2 focused suites, current provider
and restart regressions, migration equivalence, runtime-inert/schema/tool
invariants, Fast, Full, exact-head Evidence, fresh `pip check`, strict
`pip-audit` with no known vulnerabilities, stable and Engineering packaging,
amd64/arm64/arm-v7 no-push builds, exact-image 7.14.1/7.14.2/8.0.0, immutable
add-on runtime, disposable real-HA contracts, secret scan, protected paths,
whitespace, and PowerShell parsing. Every skip must be explained.

Nothing in this document authorizes publication, deployment, tagging, merging,
activation, live operational actions, production access, or a release-order
change.
