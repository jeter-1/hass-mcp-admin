# 2.1.1-beta.2 acceptance contract

Version: `2.1.1-beta.2`

Authority is limited to source and disposable-fixture validation. Live Home
Assistant access, live plans, approvals, restarts, image publication, and
deployment remain outside this contract.

## Immutable boundaries

- Base: accepted Beta 2 merge
  `6cb590079b36b6d9d9d7def5541973a27a53a424`.
- Reviewed Beta 2 source head:
  `f1a565656515aff960e02368ec85f0ffe0bac3e2`.
- Reviewed upstream releases: exact `ha-mcp` 7.14.1 and 7.14.2 registry entries.
- Expected upstream catalog fingerprint:
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
- Expected complete catalog: 45 Engineering tools plus 26 delegated reads,
  or 71 total.
- Stable v1.1.2 source, public MCP schemas, tool registration, provider
  contracts, compatibility evidence, policy classifications, dashboard
  attestations, exact operation arguments, external approval, one-dispatch
  recovery, and zero fallback must remain unchanged.

## Corrective source acceptance

Prove with offline fixtures that Supervisor self metadata containing a
repository-prefixed slug is accepted without hard-coding a repository prefix.
The resolver must call only `/addons/self/info`, use the existing injected
token, strictly decode bounded UTF-8 JSON, and reject unavailable, conflicting,
duplicate-key, non-finite, incomplete, or unsafe identity evidence.

For a requested slug exactly matching that authoritative identity, planning
must:

1. resolve the same installed add-on through the exact reviewed
   `ha_get_addon` provider;
2. persist requested/resolved identity evidence and
   `target_class=engineering_addon`;
3. capture process identity and runtime/build/tool/storage/audit evidence;
4. require the existing self-restart verification contract; and
5. make `provider_acknowledgement` unreachable, so only a changed process
   identity plus complete readiness can yield `restart_proof=process_identity`.

An exact reviewed upstream add-on must be identified by a unique full installed
slug whose documented Supervisor DNS form matches the configured MCP endpoint,
with exact reviewed admission observed over that same endpoint. Bare source
slug, prefix/suffix, display-name, version-only, and lookalike matches are
rejected. The bound target must retain
`restart_proof=upstream_readmission`; `provider_acknowledgement` is
unreachable for it. A real unrelated add-on must retain the explicitly weaker
`provider_acknowledgement` grade. Unavailable or conflicting upstream identity
fails closed, and a historical target-class conflict requires a fresh plan.

For a missing installed slug, including bare `ha_mcp` when only a
repository-prefixed upstream slug is installed, prove:

- public non-retryable `addon_not_found`;
- proposal/domain-outcome audit attribution with the requested slug;
- no plan, approval, apply, dispatch, or fallback;
- no provider operational-failure increment;
- retained available/exact provider state; and
- a separate domain-outcome count.

Also prove that authentication, connection, timeout, invalid-response,
protocol, catalog, reviewed-contract, and provider-internal failures still
degrade provider health.

## Compatibility and regression acceptance

Historical operational records without target-identity evidence must
deserialize with their exact stored plan hashes. An existing unapproved
`other_addon` record is historical evidence only: it remains readable, is not
rewritten, and apply-time revalidation must not silently change its target
class or authorize a restart.

Retain controlled reload, ordinary/upstream/self add-on restart, Home Assistant
restart, external approval, hash binding, durable dispatch intent, startup and
periodic readback-only recovery, no-blind-redispatch, Beta 1 backup,
configuration governance, entity-registry normalization, proposal audit,
dashboard reads, both exact-image lanes, and stable-v1 isolation.

Run focused lifecycle, routing, health, audit, governance, reconciliation, and
error-taxonomy tests; the complete Python suite; compilation; strict dependency
audit; metadata/YAML/PowerShell/protected-path/whitespace/evidence gates;
disposable Home Assistant contracts; exact-image 7.14.1 and 7.14.2 acceptance;
stable and Engineering builds; and amd64, arm64, and arm/v7 no-push builds.

## Later operator-controlled acceptance

Do not run these steps during source development.

1. Preserve the accepted `2.1.0-beta.2` image as rollback.
2. Deploy the exact reviewed `2.1.1-beta.2` image in place.
3. Verify version/build, 45+26=71 tools, exact 7.14.2 admission, governance and
   audit persistence, dependency readiness, and zero fallback.
4. Request an uninstalled slug and confirm non-retryable `addon_not_found`
   without provider-health degradation or plan creation.
5. Resolve the exact Supervisor slug for the running Engineering add-on.
6. Create a new self-restart proposal; do not reuse any plan created under the
   previous `other_addon` classification.
7. Confirm proposal audit identity, `target_class=engineering_addon`, process
   baseline, runtime readiness requirements, no dispatch, and exact hash-bound
   external approval.
8. Only with separate authorization, apply once and verify startup
   reconciliation produces `restart_proof=process_identity`, no redispatch,
   complete runtime/storage/audit readiness, and zero fallback.
9. Resolve the exact installed upstream slug, then create a separate plan-only
   proposal and verify `target_class=upstream_ha_mcp_addon`, the exact
   eight-field reviewed provider contract, required
   `restart_proof=upstream_readmission`, and zero dispatch. Do not approve it
   until that evidence is inspected.
10. Recheck an ordinary add-on retains
    `restart_proof=provider_acknowledgement`.
11. Roll back to the preserved Beta 2 artifact if any boundary fails.

A source-test pass is not a deployed-runtime acceptance claim.
