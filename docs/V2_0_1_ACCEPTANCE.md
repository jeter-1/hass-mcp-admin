# HA MCP Engineering Server 2.0.1 GA acceptance contract

Version: `2.0.1`

Status: post-publication acceptance authority; execution requires separate
publication and deployment authorization

This contract defines required evidence after an independently approved merge,
protected image publication, and operator-controlled deployment. Pull-request
preparation must not access a household Home Assistant, publish an image,
create a tag or release, merge, restart, or deploy.

## Promotion baseline

- Git base and PR #64 merge commit:
  `e6c57344d65f2212d8fb6e40ef78ed53acc82dbb`.
- Reviewed PR #64 head:
  `852d3eceaa2d3f34cf461083af53308156bb5f31`.
- Prior version: `2.0.1-rc1-dev2`.
- Release version: `2.0.1`.
- Recommended upstream: exact reviewed `ha-mcp` 7.14.2.
- Reviewed upstream rollback: exact reviewed `ha-mcp` 7.14.1.
- Accepted live-test environment: Home Assistant 2026.7.3.

The operator-provided deployed acceptance evidence and transition results are
recorded in the release notes. They are not a substitute for exact GA image
provenance or post-deployment readback.

## Post-merge release provenance

Before deployment, record and verify:

- exact 2.0.1 source SHA: `<required after merge>`;
- exact `v2.0.1` tag target: `<must equal release source SHA>`;
- clean build: `true`;
- exact UTC build timestamp: `<required>`;
- final image-index digest: `<required>`;
- `linux/amd64` digest: `<required>`;
- `linux/arm64` digest: `<required>`;
- `linux/arm/v7` digest: `<required>`;
- OCI revision, version, created, source, and dirty labels: `<required>`;
- version and source-SHA tags resolving to the same immutable index;
- provenance and SBOM attestations for every declared architecture; and
- successful anonymous inspection.

These values do not exist at pull-request time. Do not reuse prerelease, 2.0.0,
or upstream `ha-mcp` image digests as 2.0.1 release evidence.

## Repository and compatibility gates

1. Confirm authoritative Engineering version declarations are exactly 2.0.1.
2. Confirm the only production-runtime edit from the PR #64 merge is
   `SERVER_VERSION`.
3. Confirm `hass_mcp_admin/` has no diff and remains historical v1.1.2.
4. Confirm slug `hass_mcp_engineering_beta`, MCP port `8100`, Ingress,
   architectures, options, secrets, networking, image repository, connector
   endpoint, and persistent `/data` are unchanged.
5. Confirm public MCP input and output schemas are byte-for-byte unchanged.
6. Confirm every input, description, annotation, output, and runtime
   fingerprint is unchanged.
7. Validate both reviewed compatibility entries, their SHA-256-bound canonical
   captures, and all 156 release-specific tool-contract records.
8. Regenerate the registry and require no deterministic drift.
9. Cross-check both dashboard decisions against exact built-in attestations and
   compiled non-screenshot constraints.
10. Confirm classifications, automatic-read decisions, quarantine behavior,
    governance, approval, storage, audit, and zero-fallback policy are
    unchanged.
11. Confirm 41 Engineering tools plus 26 delegated reads produce 67 total under
    complete admission.
12. Confirm the catalog fingerprint remains:

    ```text
    c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c
    ```

13. Confirm no generic write, direct Home Assistant fallback, provider fallback,
    arbitrary forwarding, service/device execution, dashboard mutation, or new
    tool is reachable.

An unexplained schema, fingerprint, policy, admission, or tool-count difference
is a release stop condition. Do not update expected evidence merely to make a
gate pass.

## Required pre-publication validation

Require exact committed-head evidence for:

- complete Python tests with exact pass and expected-skip counts;
- Python compilation and strict dependency audit;
- metadata, YAML, secret, PowerShell, protected-path, and whitespace checks;
- Evidence-tier validation;
- stable-v1 isolation;
- compatibility registry, capture hash, regeneration, complete ledger, and
  dashboard attestation validation;
- schema snapshots, tool counts, unknown-version refusal, per-tool quarantine,
  no fallback, governance persistence, and audit behavior;
- disposable Home Assistant contracts;
- production and Engineering image builds; and
- no-push `linux/amd64`, `linux/arm64`, and `linux/arm/v7` builds.

Pull-request CI must remain read-only and must not log in to GHCR, publish,
sign, tag, release, merge, or deploy.

## Registry-derived exact-image matrix

Retain exact-image acceptance for both registry-derived releases:

1. `ha-mcp` 7.14.1; and
2. `ha-mcp` 7.14.2.

For each release require exact image-index and CI-platform digests, OCI version
and revision, server identity, protocol `2025-03-26`, 78 advertised tools, the
catalog fingerprint above, 26 admitted reads, zero contract mismatches, zero
unreviewed or prohibited delegation, normal reads, normalized errors, bounded
partial search, exact dashboard attestation and constraints, outage/recovery
behavior covered by the harness, and zero fallback.

## In-place deployment

The recommended tested pair is:

```text
HA MCP Engineering Server 2.0.1
ha-mcp 7.14.2
Home Assistant 2026.7.3
```

Home Assistant 2026.7.3 identifies the accepted live-test environment; this
document does not introduce an exclusive Home Assistant version restriction.

1. Back up the existing v2 add-on configuration and `/data`.
2. Record the running prerelease version, source SHA, image digest, selected
   compatibility entry, tool count, governance-plan count, and audit
   availability.
3. Verify the final 2.0.1 publication evidence above.
4. Update the existing add-on in place, retaining its slug, port, options,
   secrets, upstream URLs, connector endpoint, and `/data`.
5. Allow the normal add-on restart; do not restart Home Assistant.
6. Require version 2.0.1, the exact GA source SHA, clean build, successful Home
   Assistant connectivity, 41 Engineering tools, 26 delegated reads, 67 total,
   exact selected 7.14.2 compatibility, and zero fallback.
7. Verify governance plans and audit history remain readable and dependency
   prewarm completes.

No data migration or connector recreation is required.

## Compatibility transition acceptance

When the upstream version changes, delegated routes fail closed until
compatibility reconciliation admits the exact reviewed release. Automatic
reconciliation is periodic and may take up to approximately 15 minutes.
Restarting Engineering forces immediate rediscovery. No fallback is used.

During the interval:

- dashboard and delegated-read providers may report different observed
  upstream versions;
- delegated reads may fail;
- no write or fallback authority is gained; and
- operators should wait for reconciliation or restart Engineering before
  treating the failure as a connector defect.

Exercise the operator-controlled sequence
`7.14.1 -> 7.14.2 -> 7.14.1 -> 7.14.2`, verifying selected entry, 78-tool
catalog, 26 reads, zero mismatch/quarantine, dashboard status, governance and
audit persistence, and zero fallback after each reconciliation. This live
sequence requires separate authorization and is not executed in PR preparation.

## Runtime artifact provenance

Require observed server identity, version, protocol, tool count, and catalog to
match the selected release. Separately verify the running upstream container
digest and OCI revision against reviewed registry evidence. Health output must
continue to state that runtime artifact provenance is unobserved through MCP
discovery. Reviewed source/image evidence alone is not live deployment
readback.

## Error and partial-result checks

Exercise normal delegated reads plus invalid input, missing state, missing
automation, missing label/category, optional capability unavailable, bounded
partial `ha_search`, authentication, connection, timeout, and provider failure.
Verify truthful audit and health accounting, redaction, and zero fallback.

A missing `ha_get_entity` registry record remains a documented limitation. It
may return bounded generic `provider_error` / `upstream_error` /
`retryable=true`; do not claim non-retryable not-found semantics until upstream
provides a stable structured discriminator.

## Rollback acceptance

### Upstream rollback

Restore the exact reviewed 7.14.1 image. Wait for periodic reconciliation or
restart Engineering for immediate rediscovery. Verify the 7.14.1 selected entry,
78 tools, 26 reads, exact dashboard attestation, governance and audit
persistence, and zero fallback without rebuilding Engineering.

### Engineering rollback

Reinstall exact 2.0.0 image index
`sha256:d91246deab5b50749430f5194b5a9fe1473171526fe4f8551c89b1b3259ff130`
under the same add-on identity. Retain `/data`, options, secrets, and connector
endpoint. Verify version 2.0.0, source
`496006b77039a42d7a8c8f23c0bbb292f5f0ddcd`, foundation health, governance,
audit, exact admission, and zero fallback.

Stable v1.1.2 remains unchanged historical source and is not a supported v2
rollback target.

## Acceptance stop conditions

Stop on unexplained fingerprint drift, schema or count change, policy/admission
change, capture or dashboard-attestation mismatch, dependency vulnerability,
write or fallback reachability, lost persistence, stale or unverifiable
provenance, stable-v1 modification, or any live mutation outside separately
authorized acceptance.
