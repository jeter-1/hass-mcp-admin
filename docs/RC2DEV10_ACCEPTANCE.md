# RC2dev10 acceptance

## Pre-deployment gates

- Review the complete diff from baseline
  `b72c9bc57907c8332fe8be39a4faedd7a6ee2a60`.
- Require all 7.13.0, 7.14.0, and 7.14.1 built-in matrices, signed-registry,
  revocation, semantic/descriptive drift, dashboard, authentication/audit,
  governance, dependency-index, exact-image, metadata, public-schema, and
  stable-v1 isolation tests to pass.
- Confirm `2.0.0-rc2-dev9 < 2.0.0-rc2-dev10 < 2.0.0-rc.3 < 2.0.0` using
  AwesomeVersion 25.8.0.
- Require the protected promotion to publish both image tags to one verified
  multi-platform digest with revision labels equal to the merge SHA, SLSA
  provenance, SPDX SBOM, and annotated tag target verification.

## Deployment

Keep ha-mcp on 7.14.1. Update only Engineering Beta. Do not update Home
Assistant or stable v1.1.2.

## Focused live acceptance

1. Call `server_info`, `list_capabilities`, and `get_server_health`.
2. Require version `2.0.0-rc2-dev10`, the promoted merge SHA,
   `build_dirty=false`, Home Assistant 2026.7.2 connected, and catalog 40/25/0.
3. Require upstream 7.14.1, entry `ha-mcp-v7.14.1-68f386d9`, admission
   `admitted_builtin_attestation`, source `builtin`, family
   `ha_mcp_dashboard_read_v2`, valid contract, available capability, and
   `not_revoked`.
4. Require authoritative `input_contract_match`, `security_contract_match`,
   `output_contract_match`, and `runtime_contract_match` to be true.
5. Require retained `input_schema_match`,
   `reviewed_security_contract_match`, and
   `published_runtime_descriptor_match` to be true, with no semantic runtime
   descriptor drift.
6. Confirm active `trust_profile=ha_mcp_dashboard_read_v2`; no active
   `ha_mcp_7_13_dashboard_read_v1`; and no globally version-pinned description.
7. Require allowlisted upstream tool count one; only `list_dashboards` and
   `get_dashboard_config` enabled; writes, screenshots, preferences, fallback,
   and provider operational failures all false/zero.
8. Call `list_dashboards`; select `home-main-dashboard`.
9. Read that dashboard twice, first with `force_reload=true`, then false.
   Require both `config_hash` and `engineering_config_hash` to remain identical.
10. Read a nonexistent dashboard and require non-retryable
    `dashboard_not_found` without provider-health degradation or fallback.
11. Call `get_entity` for `sun.sun`; require one bounded direct-HA read and no
    upstream-admission change.
12. Reconcile `get_audit_log`: successes are success, the missing dashboard is
    failure/`dashboard_not_found`, and no dashboard payload, endpoint, or secret
    appears.
13. Call final `get_server_health`; require zero provider operational failures,
    zero fallback/prohibited fallback, unchanged governance plan count, no
    active apply, healthy storage, and unchanged runtime identity.

No step authorizes a write, service call, dashboard mutation, entity/device
mutation, restart, approval, apply, or rollback.

## Rollback conditions

Roll back only Engineering Beta to
`ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev9` if 7.14.1 admission
or dashboard reads fail, authoritative matches regress, retained fields remain
false, capability metadata remains stale, routing/security boundaries change,
fallback occurs, or promotion provenance is invalid. Do not roll back ha-mcp
7.14.1 unless that upstream provider is itself operationally unusable.
