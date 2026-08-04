# HA MCP Engineering Server 2.2.0-beta.18 acceptance

## Source boundary

- base: F3-0 head `77d8f19b3dc12ec94eef134375ddcbd5baeb2670`
- pull-request base while F3-A is not explicitly stable:
  `feature/f3-contract`
- Engineering version: `2.2.0-beta.18`
- stable version: `1.1.2`
- adapter contract: `f3-operation-adapter-v1`
- task schema: 1
- Engineering-local tools: 48
- exact protocol: `2025-03-26`
- secure pins: `aiohttp==3.14.3`, `cryptography==50.0.0`
- fallback: 0

No source or validation step may access production Home Assistant, HAOS,
Supervisor, credentials, tokens, deployed MCP endpoints, or live provider
responses.

## Required F3-C1 evidence

1. Prove exact canonical identities and fixed provider descriptors for all
   eight automation/script/input-boolean/input-number create/update paths.
2. Prove immutable proposal content and exact preservation of current
   fingerprint, proposed hash, risk evidence, F2 policy class, validation,
   provider arguments, expected result, and rollback declaration.
3. Prove every operation requests the exact exclusive resource lock and shared
   `home_assistant:core`; complete multi-operation locks must normalize before
   first dispatch.
4. Prove consumed approval, policy snapshot, provider admission, plan expiry,
   complete locks, identity, existence/absence, stale state, static validation,
   and full configuration validation fail closed before intent.
5. Prove intent persistence failure produces zero provider calls and each
   eligible operation invokes its fixed gateway no more than once.
6. Prove provider success remains nonterminal until exact resource identity,
   normalized configuration, proposed hash contract, and configuration validity
   verify through authoritative readback.
7. Prove response loss and all specified process-loss boundaries reconstruct by
   readback only, never redispatch, retain bounded evidence, and reach manual
   review at the evidence deadline.
8. Prove 1–8 ordered operation behavior, stop on first failure, exact recovery
   position, partial application, duplicate-task reuse, pre-intent cancellation,
   no compensation, and no implicit rollback.
9. Prove same-resource Engineering operations conflict and document/test the
   non-atomic external-writer window without claiming compare-and-swap.
10. Prove metrics and events exclude configuration, sequences, secrets,
    credentials, tokens, arbitrary responses, and provider error strings.

## Runtime-isolation acceptance

Require source and startup tests proving no module outside the isolated F3-C1
package imports or instantiates it, no current apply or rollback route changes,
no provider-routing or central-health integration, no new MCP tool, no current
configuration dispatch through F3-C1, and an Engineering-local tool count of
48. Task schema 1 and the current plan vocabulary must remain exact.

## F3-A and delivery-order acceptance

F3-A integration remains pending until its exact remote API commit is explicitly
declared stable. F3-C1 must then rebase or incorporate that stable API, use the
actual executor and lock records, retarget to `feature/f3-adapter-lock-core`, and
rerun every validation on the exact new head. No local-only SHA or competing
executor/lock/persistence implementation is acceptable.

This branch may not merge before the reserved Beta 17 F3-B delivery unless the
operator changes the fixed release order. Final route activation remains F3-D.

## Compatibility and validation acceptance

Exact 7.14.2 must remain 78 advertised tools, 26 delegated, zero held, 48 local,
and 74 total. Exact 8.0.0 must remain 78 advertised, 24 delegated, exactly two
held, 48 local, and 72 total. Held tools must remain exactly `ha_search` and
`ha_get_operation_status`; missing, quarantine, unreviewed, automatic-read
mismatch, and fallback counts must remain zero.

Require compilation, YAML, focused F3-C1 and current configuration/rollback
regressions, dashboard and operational-provider regressions, Fast, Full,
exact-head Evidence, fresh `pip check`, strict `pip-audit` with no known
vulnerabilities, stable and Engineering packaging, amd64/arm64/arm-v7 no-push
builds, exact-image 7.14.1/7.14.2/8.0.0, immutable add-on runtime, disposable
real-HA contracts, secret scan, protected-path validation, whitespace, and
PowerShell parsing. Record every skip; unexplained skips fail acceptance.

Nothing here authorizes publication, deployment, tagging, merging, adapter
activation, or live system access.
