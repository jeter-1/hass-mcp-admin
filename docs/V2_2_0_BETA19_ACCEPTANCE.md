# HA MCP Engineering Server 2.2.0-beta.19 acceptance

## Source boundary

- direct base: merged Beta 18 main
  `cca0d5e00d75398ec66bca0c9c2f568d11f7497e`;
- Engineering version: `2.2.0-beta.19`;
- stable version: `1.1.2`;
- canonical adapter contract: `ha_mcp_engineering.f3.contracts`, model
  `f3-operation-adapter-v1`;
- operational plan contract: 3; configuration plan contract: 2; task schema: 1;
- protocol: `2025-03-26`;
- Engineering-local tools: 48;
- secure pins: `aiohttp==3.14.3`, `cryptography==50.0.0`; and
- fallback: 0.

Production Home Assistant, HAOS, Supervisor, deployed MCP endpoints,
credentials, tokens, and live providers must not be accessed.

## Required conformance evidence

Acceptance requires all of the following:

1. All shipped C2 modules consume canonical F3 objects by identity and import
   from an isolated image-equivalent package tree without `f3_contracts`,
   `f3_dashboard`, `tests`, or checkout-root dependencies.
2. Exact capability, target, provider operation, fixed arguments, policy,
   risk, physical consequence, baseline, warnings, limitations, verification,
   and rollback-unavailable projections match current operational plan inputs.
3. The complete prepared operation uses
   `f3-operational-prepared-authority-v1`: preparation and every adapter
   boundary call one canonical payload/rehash implementation, exact derived
   fields are revalidated, and the resulting hash is independently compared
   with the authoritative durable child binding. A recomputed caller hash
   cannot replace that expected value.
4. Complete canonical lock sets match the graph in
   `F3_OPERATIONAL_CONFORMANCE.md`, including exact Beta 18 reload conflicts,
   provider self-restart union, exclusive dominance, and allowed unrelated
   domain/add-on concurrency.
5. Final locked preflight precedes caller-owned idempotent approval
   consumption. Preflight never requires consumed approval. Approval precedes
   durable intent; durable intent precedes the only provider mutation; no C2
   evidence access or write occurs between intent and provider.
6. Before claim, the executor timing must be an integer equal to the exact
   prepared duration: 86,400/900/1,800/1,800 seconds for backup/reload/add-on
   restart/HA restart. Durable intent commits exactly that intent-relative
   deadline; shorter, longer, generic, rounded, missing, or reconstructed
   replacement deadlines fail closed.
7. Lock, expiry, policy, provider, target/baseline, configuration, approval,
   and intent failures call the provider zero times. Retry reuses one consumed
   authorization authority. Every attempt has at most one provider invocation
   and one synthetic effect.
8. Canonical F3 child records remain authoritative. No independent ledger,
   task store, JSON authority, or operation worker exists. The read-only
   evidence projection is bounded; missing optional IDs cannot authorize a
   retry; corrupt/contradictory evidence fails closed. Its normalized UTC
   deadline must equal intent time plus the exact operation duration.
9. Lost-response reload cannot verify from readiness alone; add-on restart
   cannot verify from unchanged running state; HA restart requires persisted
   outage/reconnect evidence; backup may verify from an independently proven
   exact new inventory record.
10. Post-intent process reconstruction is observation/verification only,
   preserves the immutable deadline and dispatch count, and never redispatches.
11. The deadline threshold is inclusive: an unresolved operation enters manual
   review at the exact threshold. Timing is observation/escalation only;
   selective holds never expire automatically and C2 has no release authority.
12. Runtime-inert, current-route, tool-count, provider-routing, configuration,
    dashboard deferral, schema, stable-v1, exact-release, response-model,
    sanitization, and zero-fallback invariants remain unchanged.

## Compatibility

Exact 7.14.2 remains 78 advertised, 26 delegated, zero held, 48 local, and 74
total. Exact 8.0.0 remains 78 advertised, 24 delegated, two held, 48 local, and
72 total. Held tools remain exactly `ha_search` and
`ha_get_operation_status`. Exact legacy 7.14.2 and structured 8.0.0 add-on
response models, including the 71,986-byte live-equivalent fixture, must pass
without widening structured acceptance or generic response bounds.

## Validation and activation gate

Require focused canonical/adapter/lock/approval/evidence/recovery tests, every
`test_f3*.py`, operational/provider/restart regressions, compilation, YAML,
Fast, Full, clean exact-head Evidence, import/package boundaries, deterministic
registry regeneration, fresh `pip check`, strict audit with no known
vulnerabilities, secret/protected-path/PowerShell/whitespace gates, stable and
Engineering packaging, no-push architecture builds, exact-image acceptance,
immutable add-on acceptance, disposable pinned-HA contracts, publication
guards, and fully green exact-head CI. Every skip requires explanation.

Beta 19 remains ineligible for activation until F3-D provides durable child
ownership, a production authority reader and operation-evidence mapping, exact
per-operation executor construction, the central coordinator, selective hold
administration, private authenticated reconciliation, governed rollback Option
A, and route migration. Issue #92 remains separate. This document authorizes no
merge, tag, publication, image, deployment, live action, production access, or
readiness-state change.
