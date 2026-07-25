# HA MCP Engineering Server 2.0.0 GA acceptance contract

Version: `2.0.0`
Status: post-publication acceptance authority; execution requires separate
deployment authorization

This contract defines the evidence required after an independently approved
merge, protected publication, and deployment. These checks remain unexecuted
during pull-request preparation, and deployed Home Assistant access requires
separate explicit authorization.

## Accepted candidate and promotion gate

The functional baseline is Dev16 source
`247c755ccde050f0b7062a71a7fb1a7a845aaf2e`. Its protected staged-release
workflow run
[`30132025785`](https://github.com/jeter-1/hass-mcp-admin/actions/runs/30132025785)
completed successfully with:

- clean version `2.0.0-rc2-dev16`;
- build time `2026-07-24T22:58:37Z`;
- OCI index
  `sha256:8802b0561e2bdcdbd7b92c0f1d5078303d3ccb3f5a82b3e2b5bd25ed8e74c5a7`;
- `linux/amd64`
  `sha256:193ceb209623087c96905f69209de2bea914f6b3511ce9a0847fe46b75e10673`;
- `linux/arm64`
  `sha256:58600e758fa95a42371a4fe11512a8223d930b3099658d7e82bb97c1684a3cb3`;
- `linux/arm/v7`
  `sha256:5fbd9cb27d19cc5f1b011e12300b2d5510d5619d684cf5d9514ee8080f797448`;
  and
- successful anonymous image, provenance, SBOM, architecture, and tag
  verification.

Do not reuse those digests as GA evidence. Before GA deployment, record and
verify all of the following from the successful protected 2.0.0 promotion:

- exact GA source SHA: `<required after merge>`;
- exact `v2.0.0` tag target: `<must equal GA source SHA>`;
- clean build: `true`;
- exact UTC build timestamp: `<required>`;
- GA image-index digest: `<required>`;
- `linux/amd64` manifest digest: `<required>`;
- `linux/arm64` manifest digest: `<required>`;
- `linux/arm/v7` manifest digest: `<required>`;
- OCI revision/version/created/source/dirty labels: `<required and exact>`;
- source-SHA and version tags resolve to the same index;
- provenance and SBOM attestations exist for every declared architecture; and
- anonymous inspection succeeds.

Stop if any provenance value is missing, unknown, dirty, mutable, mismatched,
or not traceable to the exact accepted GA source.

## Repository and compatibility gates

Before merge and again at the exact GA source:

1. Confirm the only production-code change from Dev16 is the authoritative
   `SERVER_VERSION` value.
2. Confirm `hass_mcp_admin/` has no diff and remains version `1.1.2`.
3. Confirm the add-on slug remains `hass_mcp_engineering_beta`, MCP remains on
   port `8100`, Ingress remains on internal port `8110`, and the image
   repository remains `ghcr.io/jeter-1/hass-mcp-engineering-beta`.
4. Confirm options, option schema, secrets, networking, ingress, architecture
   list, `/data` paths, and connector endpoint are unchanged.
5. Confirm all public input/output schemas are byte-for-byte unchanged.
6. Confirm every reviewed input-schema, runtime-description, runtime-annotation,
   runtime-output-schema, and runtime-contract fingerprint is unchanged.
7. Confirm the upstream policy still has 78 entries, 26 automatic reads,
   reviewed `ha-mcp` version 7.14.1, and catalog fingerprint
   `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
8. Confirm policy classifications, argument constraints, admission,
   per-tool quarantine, dashboard trust, and no-fallback enforcement are
   unchanged.
9. Confirm the static catalog is 41 tools and full exact admission produces 67
   tools: 41 Engineering plus 26 delegated.
10. Confirm no delegated write, direct Home Assistant fallback, provider
    fallback, arbitrary forwarding, service/device execution, registry
    administration, dashboard write, or new tool is reachable.

A fingerprint difference is not expected from the version change because the
reviewed fingerprints cover upstream tool contracts, not Engineering build
identity. Stop and investigate any fingerprint change rather than updating an
expected value silently.

## Required pre-publication validation

Require clean committed-head evidence for:

- the complete Python unittest suite with exact pass and expected-skip counts;
- Python compilation;
- add-on metadata and AwesomeVersion ordering;
- all repository YAML;
- installed dependency consistency;
- bounded offline secret scanning;
- PowerShell syntax;
- protected-path authorization;
- Git and text whitespace;
- stable-v1 isolation;
- tool-count, schema, fingerprint, policy, and admission evidence;
- pinned exact-image read-gateway acceptance;
- disposable real-Home-Assistant contracts;
- production and Engineering image builds; and
- no-push builds for `linux/amd64`, `linux/arm64`, and `linux/arm/v7`.

Pull-request CI must remain read-only and must not log in to GHCR, publish,
sign, tag, merge, release, or deploy.

## In-place Dev16-to-GA upgrade

1. Back up the existing v2 add-on configuration and `/data` using the normal
   Home Assistant backup procedure.
2. Record the running Dev16 version, source SHA, image digest, tool count,
   governance-plan count, and audit availability.
3. Refresh the existing repository only after the GA promotion is complete.
4. Update the existing **HA MCP Engineering Server Beta** add-on in place.
5. Retain the same slug, port, options, access secret, upstream provider
   configuration, connector URL, and persistent `/data`.
6. Allow the normal add-on restart. Do not restart Home Assistant.
7. Reconnect or refresh the client only if its existing session does not relist
   the unchanged catalog.

There is no data migration.

## Identity, catalog, and admission checks

1. Call `server_info`.
2. Require version `2.0.0`, the exact GA source SHA, clean build, exact build
   timestamp, and successful Home Assistant connectivity.
3. Call `list_capabilities`.
4. Require 41 Engineering tools and 26 delegated automatic reads, for 67 total.
5. Call `get_server_health`.
6. Require exact reviewed `ha-mcp` 7.14.1 identity, 78 advertised upstream
   tools, catalog fingerprint
   `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`,
   and `admitted_exact`.
7. Require zero schema, description, annotation, output, runtime, identity, or
   protocol mismatches.
8. Require zero missing, unreviewed, quarantined, or collided automatic reads.
9. Require no write delegation and zero fallback, fallback success, and
   prohibited-fallback attempts.

Stop on a startup loop, terminal admission failure, stale version/SHA, or
catalog discrepancy.

## Positive-path acceptance

Exercise and verify provider, upstream version, request ID, completeness,
redaction, and zero fallback for:

1. a normal delegated state read;
2. filtered entity discovery;
3. a bounded history read;
4. a known automation configuration read;
5. dashboard inventory;
6. one exact dashboard configuration read, including the optimistic-lock and
   Engineering evidence hashes;
7. a warm exact dependency-index lookup after prewarm completes;
8. persisted governance-plan listing and exact plan retrieval; and
9. bounded audit retrieval.

Dashboard screenshots and preferences remain prohibited. No write, service,
device, reload, restart, apply, rollback, or approval operation is part of
routine GA smoke acceptance.

## Delegated error and partial-result acceptance

Use bounded disposable or specifically approved test identifiers:

1. Send delegated structured input that reaches `ha-mcp` validation. Require
   non-retryable `invalid_request`, no raw upstream prose, truthful failure
   audit, and no provider operational-failure increment.
2. Read a guaranteed nonexistent entity. Require non-retryable
   `entity_not_found` and no operational-failure increment.
3. Read a guaranteed nonexistent automation. Require non-retryable
   `automation_not_found` and no operational-failure increment.
4. Read a guaranteed nonexistent reviewed label or category resource. Require
   the exact tool-aware non-retryable `resource_not_found` mapping and no
   operational-failure increment.
5. Exercise an unavailable reviewed optional capability, if applicable.
   Require bounded non-retryable capability-unavailable semantics rather than
   connection failure.
6. Run `ha_search` with an intentionally small `config_time_budget`. When the
   scan cannot complete, require `success=true`, `partial=true`, bounded
   `partial_reason`, a non-exhaustive coverage statement, and no operational
   provider failure.
7. Compare health counters before and after. Domain, validation, capability,
   and partial outcomes must not contaminate operational provider failures.
8. Inspect matching audit records. Require exact request/tool/provider
   attribution, bounded parameters, truthful success/partial/failure status,
   and redaction of identifiers, secrets, headers, URLs, tokens, and arbitrary
   upstream prose.
9. Require fallback to remain zero throughout.

## Persistence and restart checks

After the normal add-on restart:

- existing options and secrets remain configured;
- the connector endpoint is unchanged;
- governance plan count and exact records remain readable;
- audit history remains available;
- dependency prewarm reaches `complete` with a valid index;
- exact upstream admission recovers without connector recreation;
- no startup retry remains as an active failure; and
- no migration, data rewrite, or storage corruption occurs.

## Rollback acceptance

Rollback requires separate authorization.

### Exact GA-to-Dev16 rollback

1. Reinstall the accepted Dev16 OCI index
   `sha256:8802b0561e2bdcdbd7b92c0f1d5078303d3ccb3f5a82b3e2b5bd25ed8e74c5a7`
   under the same add-on identity.
2. Retain `/data`, options, secrets, and connector endpoint.
3. Require version `2.0.0-rc2-dev16`, source SHA
   `247c755ccde050f0b7062a71a7fb1a7a845aaf2e`, and clean build readback.
4. Re-run foundation, exact-admission, governance-persistence, audit, and
   no-fallback checks.
5. Confirm no migration or data repair is required.

### Legacy v1.1.2 rollback

Stop the v2 add-on before re-enabling stable v1.1.2. Use the legacy server only
when its reduced capability set is acceptable; it does not provide v2
governance, external approval, delegated reads, dashboard reads, or v2
analyses. Do not configure conflicting ports.

## Known limitations

- Broad automation configuration-body search may remain slow when upstream
  bulk access is unavailable.
- Search time budgets can produce explicitly partial, non-exhaustive results.
- Signed generic compatibility-registry operations are deferred.
- Broader dashboard and entity/device registry administration are deferred.
- The accepted technical slug and runtime/display identity retain “beta”.
- Stable v1.1.2 is retained temporarily as a reduced-capability rollback
  option.

Acceptance fails on unexplained fingerprint drift, schema or tool-count change,
upstream policy/admission change, operational-counter contamination, leaked
content, fallback, write reachability, lost persistence, stale provenance,
stable-v1 modification, or any required live mutation outside separately
authorized acceptance.
