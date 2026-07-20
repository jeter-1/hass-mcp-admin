# RC2dev10 release notes

Version: `2.0.0-rc2-dev10`
Baseline: `b72c9bc57907c8332fe8be39a4faedd7a6ee2a60`

## Purpose

RC2dev9 correctly admitted and used live ha-mcp 7.14.1 through built-in entry
`ha-mcp-v7.14.1-68f386d9`. Dashboard inventory, two exact configuration reads,
stable upstream and Engineering hashes, missing-dashboard taxonomy, audit
truth, and direct Home Assistant reads passed live acceptance. The defect was
limited to retained observability and capability metadata: legacy comparisons
still used static 7.13.0 expectations, and the capability catalog still named
the deprecated single-release profile.

## Correction

RC2dev10 extends each release attestation with optional informational evidence
for the exact raw input schema, reviewed-security descriptor, reviewed fixture
runtime descriptor, and published runtime descriptor. After the existing
admission algorithm selects an exact built-in or signed entry, the provider
uses that entry for the retained expected fingerprint fields. Older signed
entries remain readable. Missing informational evidence cannot enable or block
admission and is never replaced with another release's values.

The fingerprint families remain separate:

- Raw schema: exact published `inputSchema` representation.
- Reviewed security descriptor: exact safety projection retained by RC3A.
- Fixture and published runtime descriptors: exact reviewed tool
  representations used for diagnostic drift.
- Normalized input/security/output/runtime contracts: the authoritative
  semantic admission gate.
- Catalog fingerprint: unrelated-tool inventory evidence only.

The active trust profile is now reported as `ha_mcp_dashboard_read_v2`.
Engineering remains implementation-independent: exact upstream releases are
reviewed through built-in or signed attestations; no generic version range is
trusted.

## Security and compatibility

This release changes no admission decision, normalizer, signature validation,
registry sequence/cache behavior, tool route, argument builder, output parser,
hash contract, fallback policy, public MCP input schema, governance record, or
stable-v1 file. Only `ha_config_get_dashboard` remains allowlisted. Engineering
can construct only bounded inventory and exact-path non-screenshot reads.
Writes, screenshots, preference persistence, service execution, entity/device
administration, arbitrary forwarding, and generic Standard HA MCP delegation
remain unavailable.

## Release and rollback

After approval and merge, the protected promotion workflow must build the exact
merge commit; publish `2.0.0-rc2-dev10` and `sha-<merge-sha>` to one amd64,
arm64, and arm/v7 index; attach SLSA provenance and the Engineering SPDX SBOM;
create annotated tag `v2.0.0-rc2-dev10`; verify its target; and report
`release_complete=true`.

Primary rollback image:

`ghcr.io/jeter-1/hass-mcp-engineering-beta:2.0.0-rc2-dev9`

Keep ha-mcp on 7.14.1 during promotion, acceptance, and any Engineering-only
rollback. RC2dev8 is a secondary operational rollback only when a separate
reason requires it.
