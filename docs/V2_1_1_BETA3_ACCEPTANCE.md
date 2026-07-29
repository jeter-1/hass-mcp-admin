# 2.1.1-beta.3 acceptance contract

Version: `2.1.1-beta.3`

Authority is limited to source and disposable-fixture validation. Do not
access live Home Assistant, create a live plan or approval, restart a service,
publish an image, or deploy from this contract.

## Immutable boundaries

- Base: accepted `2.1.1-beta.2` merge
  `5038c03afc86950219533245c4181881a32186e8`.
- Expected complete catalog: 45 Engineering tools plus 26 delegated reads,
  or 71 total.
- Expected upstream catalog fingerprint:
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
- Stable v1.1.2, public MCP schemas, tool registration, exact reviewed
  admission, add-on identity, approval authority, plan hashing, dispatch
  providers, and zero fallback remain unchanged.

## Corrective source acceptance

Use a storage-backed governed Home Assistant restart fixture to prove:

1. planning and immediate pre-dispatch configuration validation are valid;
2. external approval is consumed and dispatch intent is persisted before the
   one provider call;
3. a direct post-dispatch Home Assistant Core timeout or unavailability is
   recorded with `outage_observed=true`, the earliest and latest timestamps,
   an observation count, a bounded evidence source and failure category, and
   the immutable observation deadline exactly 180 seconds after the original
   persisted dispatch;
4. repeated unavailable probes merge without losing earlier evidence;
5. a later successful Core identity read adds
   `home_assistant_reconnected=true`, its explicit `reconnected_at` timestamp,
   unchanged identity, valid configuration,
   runtime/catalog/storage/audit/upstream/dependency readiness, and
   zero-fallback evidence;
6. the same plan reaches `applied_verified` and
   `restart_home_assistant_and_verified`; and
7. dispatch attempt and provider action counts remain one.

Prove separately that a confirmed provider response followed only by successful
availability probes remains pending. Pre-dispatch failures and unrelated
upstream-provider failures must not create outage evidence. A Supervisor proxy
502, 503, or 504 on the direct Core identity read is availability evidence;
other API errors are not.

The initial active probe budget remains 15 attempts at one-second intervals,
approximately 15 seconds. The independently persisted outage-evidence
eligibility interval is 180 seconds from the original dispatch time. Use
deterministic boundary clocks to prove observations at `T`, `T+14s`, `T+60s`,
and exactly `T+180s` qualify, while observations before `T` or after `T+180s`
do not. Prove the deadline is exactly recomputed and that missing, malformed,
shortened, or widened deadlines fail closed. Reconciliation and process
recreation must preserve and never extend the deadline.

Include an explicit late-onset scenario: Core remains reachable throughout the
initial 15-second probe loop, becomes unavailable at `T+60s`, and later
recovers successfully without redispatch. Recreate the service after the
deadline and prove a future unrelated Core outage cannot verify the old plan.
Also prove that an outage qualified at `T+179s` survives service recreation and
permits later recovery after `T+180s`. Recovery has no eligibility deadline
once qualified outage evidence exists. A raw or malformed
`outage_observed=true` record must fail the same complete-evidence predicate
used for newly observed outages and must not skip a required probe while the
interval remains open.

Prove `reconnected_at` is absent before a successful post-outage Core identity
read, is not inferred from dispatch acknowledgement or the reconnection
boolean, survives serialization and process recreation, and never regresses to
null or a later replacement timestamp.

Recreate the governance service against retained storage after an outage has
been persisted. Startup reconciliation must complete the same plan using
readback only, retain the exact plan hash, and perform zero restart dispatches.
Repeated apply while pending must also be readback-only, and an exact apply
after completion must return `already_applied`.

## Regression acceptance

Retain Engineering self-restart `process_identity`, upstream restart
`upstream_readmission`, authoritative endpoint-to-slug binding, ordinary
add-on acknowledgement, missing-add-on domain outcomes, controlled reload,
backup governance, principal separation, stale-state protection, exact-image
7.14.1 and 7.14.2 acceptance, disposable Home Assistant contracts, and stable
v1 isolation.

Run focused lifecycle and reconciliation tests, the complete Python suite,
compilation, metadata and YAML validation, dependency and secret checks,
protected-path and whitespace gates, disposable Home Assistant contracts,
both exact-image lanes, stable and Engineering builds, and amd64, arm64, and
arm/v7 no-push builds.

## Later operator-controlled acceptance

Do not run these steps during source development.

1. Preserve the accepted `2.1.1-beta.2` artifact as rollback.
2. Deploy the exact reviewed `2.1.1-beta.3` artifact in place.
3. Verify version/build, 45+26=71 tools, exact upstream admission, healthy
   governance and audit storage, dependency readiness, and zero fallback.
4. Create and externally approve one exact Home Assistant restart plan only
   with separate operational authorization.
5. Apply once, tolerate expected Core unavailability, and read the same plan
   until it is terminal.
6. Verify persisted outage and reconnection evidence, one dispatch attempt,
   `applied_verified`, `restart_home_assistant_and_verified`, restored runtime
   checks, and zero fallback.
7. Repeat exact apply and require `already_applied` with no redispatch.
8. Roll back to the preserved Beta 2 artifact if any boundary fails.

`sensor.uptime` may be useful as independent operator evidence but is not a
production verification dependency.
