# HA MCP Engineering Server 2.2.0-beta.18 acceptance

## Source boundary

- direct base: merged Beta 17 main
  `1815f7aabeb09eefeb86bbca1108c5cea537da5d`
- canonical F3 package: `ha_mcp_engineering.f3.contracts`
- canonical executor and locks: merged Beta 17 F3-A
- Engineering version: `2.2.0-beta.18`
- stable version: `1.1.2`
- adapter contract: `f3-operation-adapter-v1`
- task schema: 1
- configuration plan contract: 2
- operational plan contract: 3
- Engineering-local tools: 48
- exact protocol: `2025-03-26`
- secure pins: `aiohttp==3.14.3`, `cryptography==50.0.0`
- fallback: 0

No source or validation step may access production Home Assistant, HAOS,
Supervisor, credentials, tokens, deployed MCP endpoints, or live provider
responses.

## Required F3-C1 evidence

1. Prove canonical shipped imports and image-equivalent import closure without
   the repository root, tests, or compatibility facade.
2. Prove exact canonical identities and fixed provider descriptors for all
   eight automation/script/input-boolean/input-number create/update paths.
3. Prove immutable planning equivalence: plan/operation identity, fingerprints,
   hashes, risk/policy/approval evidence, effects, provider arguments,
   validation, verification, rollback unavailable, and lock-set hash.
4. Prove every capability requests exclusive exact resource, shared matching
   reload-domain, and shared `home_assistant:core` locks, all resource scope,
   with no `ha-mcp` add-on lock.
5. Prove the merged executor orders locks, final preflight, idempotent approval
   consumption, durable intent, and at most one fixed gateway mutation.
6. Prove stale, target, admission, validation, lock-conflict, and pre-intent
   cancellation refusals consume no approval; approval and intent failure call
   the gateway zero times.
7. Prove provider success is nonterminal until exact identity, normalized
   configuration, resulting hash, and configuration validity verify.
8. Prove response loss and process reconstruction retain `dispatch_count=1`,
   gateway mutation count at most one, recovery mutation count zero, and the
   unchanged evidence deadline.
9. Prove the 1–8 sequence model creates deterministic child descriptors only,
   persists and dispatches nothing, retains one public task, blocks later work
   after an unresolved operation, and never authorizes redispatch.
10. Prove cancellation succeeds only before the first intent, forward rollback
    is unavailable for all eight capabilities, and legacy rollback routing is
    unchanged.
11. Prove matching reload/restart conflicts, permitted unrelated-resource
    concurrency, and the documented non-atomic external-writer limitation.
12. Prove bounded metrics/events and absence of arbitrary gateway operations,
    service data, dynamic loading, fallback, physical-device actions, dashboard
    setters, and runtime activation.

## Multi-operation integration prerequisite

Merged F3-A supports one durable execution record for one prepared operation.
Beta 18 therefore does not claim executable multi-operation durability. F3-D
or a separately accepted prerequisite must supply one durable child execution
identity per ordered operation, bound to public task ID, plan ID, operation ID,
and attempt ID, without creating multiple public tasks or hiding multiple
writes behind one F3 intent.

## Runtime-isolation acceptance

Require source and startup tests proving current application, governance,
task-recovery, provider-routing, capability, and tool-registration modules do
not import or instantiate C1. Current create/apply/rollback/cancel routes stay
legacy-authoritative. There is no startup listener, coordinator, repository,
public tool, dashboard executor, or current C1 dispatch route.

## Compatibility and validation acceptance

Exact 7.14.2 remains 78 advertised, 26 delegated, zero held, 48 local, and 74
total. Exact 8.0.0 remains 78 advertised, 24 delegated, exactly two held, 48
local, and 72 total. Held tools remain exactly `ha_search` and
`ha_get_operation_status`; missing, quarantine, unreviewed, automatic-read
mismatch, and fallback counts remain zero.

Require compilation, YAML, focused and all-F3 suites, configuration/rollback,
dashboard and operational-provider regressions, Fast, Full, clean exact-head
Evidence, image import closure, import boundaries, registry determinism, fresh
`pip check`, strict audit with no known vulnerabilities, secret/protected-path/
PowerShell/whitespace checks, stable and Engineering packaging, three no-push
architectures, exact images, immutable add-on acceptance, disposable pinned HA
contracts, and publication guards. Every skip must be explained.

Nothing here authorizes publication, deployment, tagging, merging, adapter
activation, or live system access.
