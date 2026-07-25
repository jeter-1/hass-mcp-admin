# HA MCP Engineering Server 2.0.0 GA release notes

Version: `2.0.0`
Status: GA promotion candidate; not tagged, published, merged, or deployed

This release promotes the accepted `2.0.0-rc2-dev16` runtime without changing
its functional behavior. The accepted candidate was merged as
`247c755ccde050f0b7062a71a7fb1a7a845aaf2e`, published by protected promotion
workflow run
[`30132025785`](https://github.com/jeter-1/hass-mcp-admin/actions/runs/30132025785),
and passed the separately reported deployed-runtime smoke test with documented
limitations.

The GA release commit, tag, and image digest do not exist while this pull
request is under review. After a separately authorized merge, the existing
pre-versioned promotion workflow must bind `v2.0.0`, the version image, the
source-SHA image, OCI labels, SBOM, provenance, and all architecture manifests
to the exact resulting `main` commit.

## Accepted Dev16 provenance

- Version: `2.0.0-rc2-dev16`
- Source and release SHA:
  `247c755ccde050f0b7062a71a7fb1a7a845aaf2e`
- Build timestamp: `2026-07-24T22:58:37Z`
- Image:
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev16`
- Source image:
  `ghcr.io/jeter-1/hass-mcp-engineering-beta:sha-247c755ccde050f0b7062a71a7fb1a7a845aaf2e`
- OCI image-index digest:
  `sha256:8802b0561e2bdcdbd7b92c0f1d5078303d3ccb3f5a82b3e2b5bd25ed8e74c5a7`
- `linux/amd64` manifest:
  `sha256:193ceb209623087c96905f69209de2bea914f6b3511ce9a0847fe46b75e10673`
- `linux/arm64` manifest:
  `sha256:58600e758fa95a42371a4fe11512a8223d930b3099658d7e82bb97c1684a3cb3`
- `linux/arm/v7` manifest:
  `sha256:5fbd9cb27d19cc5f1b011e12300b2d5510d5619d684cf5d9514ee8080f797448`
- Build state: clean
- Release tag: `v2.0.0-rc2-dev16`
- Publication, anonymous verification, provenance, SBOM, and tag
  reconciliation: complete

These values identify the accepted rollback candidate. They are not the future
GA image provenance.

## Delivered v2 architecture

The Engineering server is the single client-visible facilitator. It owns
engineering analysis, evidence, governance, approval, verification, rollback,
audit, and handoff semantics while delegating only exact reviewed pure reads to
the standard `ha-mcp` provider.

The fully admitted catalog remains:

- 25 canonical Engineering tools;
- 16 additive Engineering-native tools;
- 41 statically registered Engineering tools;
- 26 dynamically delegated automatic reads; and
- 67 total tools.

The reviewed upstream remains exact `ha-mcp` 7.14.1 with its 78-tool advertised
catalog and reviewed catalog fingerprint
`c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
Delegated writes, mixed operations, unreviewed tools, direct Home Assistant
fallback, and provider fallback remain prohibited.

The v2 capability set includes:

- constrained dashboard inventory and exact configuration reads;
- immutable configuration planning with external Home Assistant administrator
  approval;
- exact readback verification and separately approved rollback;
- dependency, automation reliability, change impact, configuration integrity,
  incident correlation, and handoff analyses;
- dependency-index prewarming and bounded freshness behavior;
- persistent governance plans and audit history under `/data`;
- bounded, redacted evidence with provider and upstream attribution;
- truthful complete, partial, domain, validation, capability, authentication,
  connection, timeout, and provider-failure outcomes; and
- Dev16's strict delegated-error normalization, including tool-aware reviewed
  not-found mappings and fail-closed duplicate/non-finite JSON rejection.

## Compatibility and functional freeze

GA changes only authoritative version declarations, release documentation, and
the offline exact-document authority convention required by the existing
pre-versioned promotion workflow.

It preserves:

- add-on slug `hass_mcp_engineering_beta`;
- add-on directory and image repository;
- runtime server ID `hass-mcp-engineering-beta`;
- MCP port `8100`;
- administrator-only Ingress port `8110`;
- connector endpoint and authenticated MCP path;
- all options, schemas, secrets, and defaults;
- `/data` audit and governance persistence;
- dashboard and upstream-read provider configuration;
- all public MCP input/output schemas;
- reviewed descriptions, annotations, output contracts, runtime contracts, and
  policy classifications;
- exact upstream admission and per-tool quarantine;
- all delegated-error, health-counter, audit, redaction, and partial-result
  semantics;
- dashboard, dependency-index, governance, approval, apply, verification, and
  rollback behavior; and
- stable v1.1.2 under `hass_mcp_admin/`.

No data migration or connector recreation is required. A normal add-on restart
occurs as part of the Home Assistant add-on update.

## Naming decision

The displayed add-on name and runtime name retain “Beta” for 2.0.0.
`SERVER_NAME` and `SERVER_ID` are operational identity in `server_info`,
health, capabilities, and handoff evidence, while the Home Assistant add-on
name is pinned with the existing slug by release validation. Renaming only one
surface would create inconsistent identity; renaming all surfaces would change
accepted runtime behavior and add migration risk. Human-facing naming cleanup
is therefore deferred.

## In-place Dev16 upgrade

After merge, successful protected publication, and separate deployment
authorization:

1. Record the exact GA source SHA and verified GA image-index digest.
2. Refresh the existing add-on repository.
3. Update the existing **HA MCP Engineering Server Beta** installation in
   place from Dev16 to 2.0.0.
4. Retain its slug, port, access secret, upstream provider options, audit
   options, and governance `/data`.
5. Allow the normal add-on restart; do not restart Home Assistant itself.
6. Keep the existing connector URL.
7. Run the post-install checks in
   [`V2_0_0_ACCEPTANCE.md`](V2_0_0_ACCEPTANCE.md).

The required checks include version `2.0.0`, exact GA build SHA, clean build,
Home Assistant connectivity, 67 tools, exact `ha-mcp` 7.14.1 admission, zero
contract mismatches/quarantine, zero fallback, readable governance plans, and
available audit history.

## Rollback

### Immediate GA rollback

Reinstall the exact accepted Dev16 image:

`ghcr.io/jeter-1/hass-mcp-engineering-beta@sha256:8802b0561e2bdcdbd7b92c0f1d5078303d3ccb3f5a82b3e2b5bd25ed8e74c5a7`

Retain the same add-on identity and `/data`. Verify version
`2.0.0-rc2-dev16`, source SHA
`247c755ccde050f0b7062a71a7fb1a7a845aaf2e`, foundation health, exact
upstream admission, governance-plan readability, and audit persistence.

### Legacy rollback

When the reduced capability set is acceptable, stop the v2 Engineering add-on
and re-enable stable v1.1.2 on port `8099`. Do not run both servers on a
conflicting port. Stable v1.1.2 does not contain v2 governance, external
approval, dashboard-read, dependency-index, or delegated-read capabilities.

No rollback migration script is required or added.

## Known limitations and deferred work

- Broad automation configuration-body search can remain materially slower when
  upstream bulk access is unavailable.
- `config_time_budget` bounds configuration fetching rather than guaranteeing a
  strict end-to-end deadline; a partial result is explicitly non-exhaustive.
- Signed generic compatibility-registry operations are deferred.
- Broader dashboard administration and entity/device registry administration
  are deferred.
- The technical add-on slug, image repository, server ID, and displayed name
  retain “beta” to avoid identity and migration changes.
- Stable v1.1.2 remains available temporarily as a legacy rollback option with
  a smaller capability set.

Deferred work is not included in 2.0.0.

## Explicit non-actions

Preparing this promotion does not create a tag or GitHub release, publish or
retag an image, update a stable channel, merge a pull request, deploy an
add-on, restart Home Assistant, access a live Home Assistant environment, or
change repository settings.
