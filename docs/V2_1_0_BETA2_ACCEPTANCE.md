# 2.1.0-beta.2 acceptance contract

Version: `2.1.0-beta.2`

Authority is limited to source and disposable-fixture validation. Deployment,
live restarts, live add-on actions, and live approvals remain outside this
contract.

## Provenance and immutable boundaries

- Version: `2.1.0-beta.2`
- Source: record the exact reviewed pull-request head.
- Base: accepted Beta 1 source
  `d3851a1a1844b06b82f5b8d18ce9499b24acd46a`.
- Reviewed upstream releases: exact `ha-mcp` 7.14.1 and 7.14.2 registry entries.
- Expected upstream catalog fingerprint:
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
- Expected catalog after complete exact admission: 45 Engineering plus 26
  delegated reads, or 71 total.
- Stable v1.1.2, ports, slug, `/data`, public canonical schemas, reviewed
  upstream tool fingerprints, classifications, dashboard attestations, and
  zero-fallback policy must remain unchanged.

## Source acceptance

Validate that each proposal performs no operational dispatch, exposes a
bounded schema, produces an immutable contract-v3 plan, requires external
administrator approval with principal separation, and binds the exact target
and provider evidence.

For every operation, prove:

1. dispatch intent and approval consumption persist before provider invocation;
2. the fixed reviewed arguments are the only reachable action;
3. one plan permits no more than one dispatch;
4. provider loss after dispatch enters readback-only recovery;
5. repeated apply and startup reconciliation never redispatch;
6. success requires operation-specific evidence;
7. incomplete evidence remains pending or indeterminate;
8. audit, health, attribution, redaction, and zero fallback remain truthful.

Reload acceptance covers all four allowed domains, invalid domains, unavailable
services, planning and apply-time configuration validation, readable domain
state, and post-dispatch verification failure.

Add-on acceptance covers exact installed identity, stale target refusal, fixed
restart action, expected temporary unavailability, running-without-restart-
evidence refusal, Engineering self-restart process identity, exact `ha_mcp`
readmission, and no start/stop/install/update/configuration/proxy reachability.

Home Assistant acceptance covers invalid-config refusal, durable intent before
simulated disconnect, exact single dispatch, bounded pending response,
Home Assistant and Engineering recovery, build and 71-tool restoration,
governance/audit persistence, exact upstream admission, dependency recovery,
post-restart validation, and no inference from connectivity alone.

## Regression and evidence gates

Run the complete Python suite, compilation, strict dependency audit, metadata
and YAML validation, PowerShell gates, secret scan, protected-path validation,
whitespace validation, stable-v1 comparison, evidence gate, disposable Home
Assistant contracts, both exact-image upstream lanes, production and
Engineering image builds, and amd64, arm64, and arm/v7 no-push builds.

Both exact-image lanes must retain 78 advertised upstream tools, 26 admitted
reads, zero mismatches, zero unreviewed exposure, dashboard constraints, error
normalization, bounded partial search, outage recovery, and zero fallback.

## Later operator-controlled runtime acceptance

Do not execute these steps during source development.

1. Preserve exact Beta 1 and upstream 7.14.2 rollback artifacts.
2. Deploy the reviewed Beta 2 image in place.
3. Verify version/build cleanliness, Home Assistant connectivity, 45
   Engineering tools, 26 delegated reads, 71 total, exact upstream admission,
   catalog fingerprint, governance/audit persistence, and dependency prewarm.
4. For each new operation, create a proposal and confirm no action occurred.
5. Obtain a distinct administrator approval bound to the exact plan hash.
6. Apply one controlled reload and verify validation, one dispatch, readback,
   audit, metrics, and no redispatch.
7. With separate authorization, restart a disposable noncritical add-on and
   verify exact identity, version, state transition or provider completion, and
   one dispatch.
8. Separately authorize Engineering self-restart and verify automatic recovery
   of the original plan without redispatch.
9. Separately authorize upstream `ha_mcp` restart and verify exact readmission.
10. Separately authorize Home Assistant restart and verify the complete
    recovery contract, governance/audit continuity, dependency recovery, and
    zero fallback.
11. Exercise invalid config, stale add-on, response loss, pending verification,
    and repeated apply without a second action.
12. Roll back the Engineering image to Beta 1 if any acceptance boundary fails;
    retained operational records remain available to 2.1.

Record plan IDs/hashes, approval principals, request IDs, provider operation
evidence, audit records, before/after counters, exact image digests, and final
outcomes. A source-test pass is not a deployed-runtime acceptance claim.
