# RC2dev9 acceptance checklist

This checklist is read-only until a separately reviewed deployment decision.
Do not merge, tag, publish, or deploy from pull-request validation.

## Repository and release gates

1. Confirm the branch is based on
   `4ae05328bf70d9b603554cb7333c3126aea10b4c`.
2. Confirm version `2.0.0-rc2-dev9` agrees in add-on metadata, runtime metadata,
   validation assertions, changelog, and documentation.
3. Confirm stable v1.1.2 has no diff.
4. Run compile, YAML/metadata, full tests, exact public-schema comparison,
   workflow security tests, Docker declared-architecture validation, and
   `git diff --check`.
5. Require `40 / 25 / 0`, schema version 1, no governance-store migration, and
   no pull-request registry login or image push.

## Built-in release admission

For fixtures representing exact `ha-mcp` 7.13.0, 7.14.0, and 7.14.1:

1. Initialize and list tools.
2. Require `admitted_builtin_attestation` and family
   `ha_mcp_dashboard_read_v2`.
3. Require all four normalized fingerprint matches.
4. List dashboards and read one exact dashboard twice.
5. Require only fixed non-screenshot arguments and stable upstream/Engineering
   hashes.
6. Confirm missing dashboard remains a domain outcome and does not degrade
   provider health.

Reject before `tools/call` when identity, version, protocol, tool name, input
semantics, safety annotation, output contract, runtime fingerprint, hash format,
or response structure drifts. An unknown release may trigger exactly one bounded
registry refresh/retry; it remains unavailable unless an exact valid attestation
then matches.

## Registry security

1. Verify a correct detached Ed25519 signature.
2. Reject tampering, a wrong key, duplicate JSON keys, malformed/oversized data,
   expiry, lower sequence, equal-sequence conflicting content, and revocation.
3. Confirm atomic cache write, restart recovery, refresh cadence, and cache hard
   age.
4. Confirm refresh failure preserves last-known-good data.
5. Confirm higher-sequence revocation takes effect without code changes.
6. Confirm endpoint URL, public-key value, registry body, signature, cache path,
   and raw exceptions are absent from MCP responses and bounded health.
7. Confirm valid built-in entries continue working when registry refresh fails.

## Capability containment

Require exactly one upstream tool allowlist member:
`ha_config_get_dashboard`. Prove all of these reject before network dispatch:

- `ha_set_entity`, `ha_set_device`;
- `ha_call_service`, `call_service`, batch/bulk service tools;
- dashboard set/delete and backup tools;
- screenshot/rendering operations;
- arbitrary tool names and raw argument dictionaries.

Confirm the public tool count and schemas are unchanged, generic Standard HA MCP
delegation remains unavailable, direct-HA exception policies are unchanged, no
fallback occurs, and no registry data can enable an uncompiled family.

## Manual future-version workflow dry run

After the protected environment is configured, dispatch
`Prepare ha-mcp compatibility attestation` with one exact stable version. Require:

- fixed official source and image locations;
- exact tag/source/image/provenance evidence;
- disposable Home Assistant startup and exact image-by-digest runtime read;
- stable fingerprints and dashboard hashes;
- no screenshot or write dispatch;
- a signed data-only diff and draft PR;
- no package permission, tag, release, deployment, image push, or Engineering
  promotion.

Test negative cases for non-stable version input, missing tag/image, wrong image
version/revision, source/runtime mismatch, contract drift, missing provenance,
signature failure, and existing/revoked conflicting entry.

## Post-deployment read-only acceptance

After a later reviewed merge and normal Engineering promotion:

1. Update only Engineering Beta.
2. Call `server_info`; verify version, exact release SHA, UTC build time, and
   `build_dirty=false`.
3. Call `list_capabilities`; verify `40 / 25 / 0`.
4. Call `get_server_health`; verify HA/governance health, zero fallback, the
   observed upstream version, admission source/status, family, attestation ID,
   four fingerprint matches, and registry/cache state.
5. Run `list_dashboards` and two unchanged reads of one non-critical dashboard;
   require identical dual hashes and no rendering fields.
6. Probe one unknown upstream write tool in the disposable harness only; require
   rejection before dispatch.
7. Inspect logs/audit/health for endpoint, secret, schema, registry, or signature
   leakage.

No Home Assistant, dashboard, entity/device registry, service, automation,
governance, add-on, or host write is part of RC2dev9 acceptance.

## Rollback

Rollback only Engineering Beta to immutable
`ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev8`. Leave stable v1.1.2
unchanged. Remove or disable the optional registry settings only if registry
configuration itself caused startup validation to fail; built-in attestations do
not require the registry.
