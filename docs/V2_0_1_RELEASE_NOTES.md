# HA MCP Engineering Server 2.0.1 GA release notes

Version: `2.0.1`

Status: GA promotion candidate; not tagged, published, merged, or deployed

This release promotes the accepted `2.0.1-rc1-dev2` runtime without changing
functional behavior, public MCP schemas, provider contracts, compatibility
policy, or tool registration. The exact Git base is the merge of PR #64 on
`main`; final 2.0.1 source and image provenance do not exist while this pull
request is under review.

The final tag, image-index digest, architecture manifests, OCI labels, SBOM, and
provenance are post-merge release evidence. They must be recorded from the
separately authorized protected publication workflow and must not be inferred
from prerelease or pull-request builds.

## Delivered dependency and SDK hardening

2.0.1 retains the accepted RC1-dev1 dependency boundary:

- MCP SDK 1.28.1, upgraded from 1.9.0;
- strict dependency auditing with no advisory exceptions;
- fail-closed integration with the pinned SDK's private FastMCP registry;
- transactional dynamic-registry replacement, post-checking, and restoration;
- preserved MCP server capability behavior and reviewed protocol
  `2025-03-26`; and
- no generic upstream write delegation, arbitrary forwarding, direct Home
  Assistant fallback, or provider fallback.

The production FastMCP bind remains `0.0.0.0`. CVE-2025-66416 remains a
mitigated, deferred configuration risk tracked by issue #62. Host or Origin
enforcement is not added by this release-only promotion.

## Reviewed upstream updateability

The compiled, source-controlled registry contains exact reviewed entries for
`ha-mcp` 7.14.1 and 7.14.2. Each release has 78 release-specific tool-contract
records, for 156 records total, including exact input, description, annotation,
output, and runtime fingerprints plus separately human-owned policy.

For either completely matching release:

- 26 pure reads are explicitly approved for automatic delegation;
- 41 Engineering tools plus 26 delegated reads produce 67 registered tools;
- unknown upstream versions fail closed;
- new tools remain unreviewed and unavailable;
- changed reviewed reads are quarantined independently;
- removed reads are reported rather than substituted;
- writes and mixed operations remain blocked; and
- no fallback is used.

The reviewed catalog fingerprint for both releases is:

```text
c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c
```

The recommended GA deployment pair is:

```text
HA MCP Engineering Server 2.0.1
ha-mcp 7.14.2
Home Assistant 2026.7.3
```

Home Assistant 2026.7.3 is the accepted live-test environment, not a new
exclusive compatibility restriction. Exact reviewed `ha-mcp` 7.14.1 remains
the tested upstream rollback release.

## Compatibility evidence assurance

Each registry entry is bound to a canonical committed capture by SHA-256.
Repository validation regenerates the catalog, error shapes, and every per-tool
contract fingerprint, then joins those capture-derived facts to human-owned
classifications and automatic-read decisions. Generated candidates do not
authorize policy.

Dashboard decisions are independently bound to exact built-in attestations and
the compiled non-screenshot argument constraints. The exact-image CI matrix is
generated from the reviewed registry and verifies the image-index digest,
platform digest, OCI version, and OCI revision before starting either upstream
container.

## Truthful provenance

Observed MCP identity, version, protocol, tool count, and catalog are runtime
facts. Source commits, image-index and platform digests, and OCI revisions are
reviewed registry evidence. MCP discovery does not independently observe the
running upstream artifact. Live deployment digest and revision verification is
an operator responsibility and remains a required acceptance step.

## Operator-provided acceptance evidence

The operator reports successful deployed operation with:

| Evidence | Operator-provided value |
| --- | --- |
| Engineering version | `2.0.1-rc1-dev2` |
| Engineering build SHA | `e6c57344d65f2212d8fb6e40ef78ed53acc82dbb` |
| Home Assistant | `2026.7.3` |
| Current upstream | `ha-mcp 7.14.2` |
| Selected compatibility entry | `ha-mcp-v7.14.2-7917b2d3` |
| Reviewed upstream source | `904c14ebbe76de700f7c3535f5cc71c017dca12e` |
| Reviewed upstream image index | `sha256:7917b2d385e16e43f45f92fc72a757e5c0aec8d88b3cd69fe64f3b5106cbfe36` |
| Reviewed rollback entry | `ha-mcp-v7.14.1-68f386d9` |
| Upstream advertised tools | 78 |
| Engineering / delegated / total | 41 / 26 / 67 |
| Fallback | zero |

The operator also reports successful reviewed transitions:

```text
7.14.1 -> 7.14.2 -> 7.14.1 -> 7.14.2
```

Both directions reconciled without rebuilding Engineering. These are accepted
operator-provided live results, not source or CI checks independently executed
by Codex.

## Reconciliation behavior

When the upstream `ha-mcp` version changes, delegated routes fail closed until
the reviewed release is admitted by compatibility reconciliation. Automatic
reconciliation runs periodically and may take up to approximately 15 minutes.
Restarting the Engineering add-on forces immediate rediscovery. No fallback is
used during the transition.

The dashboard and delegated-read providers may temporarily report different
upstream versions, and delegated calls may fail during the interval. This is an
availability limitation, not a write-safety relaxation. Operators should wait
for reconciliation or restart Engineering before treating the failure as a
connector defect.

## In-place upgrade

After an independently approved merge, the protected publication workflow
builds and publishes the final 2.0.1 image, records its SBOM and provenance,
and creates and pushes the annotated `v2.0.1` tag.

1. Record the exact 2.0.1 source SHA, image-index digest, architecture digests,
   OCI labels, provenance, SBOM, and annotated-tag evidence.
2. Update the existing **HA MCP Engineering Server Beta** installation in place
   to the exact published 2.0.1 artifact.
3. Retain slug `hass_mcp_engineering_beta`, MCP port `8100`, Ingress, options,
   secrets, connector endpoint, and persistent `/data`.
4. Allow the normal add-on restart; do not restart Home Assistant.
5. Verify the deployed version, build revision, and image provenance, then run
   the final GA acceptance test in
   [`V2_0_1_ACCEPTANCE.md`](V2_0_1_ACCEPTANCE.md).
6. Create the GitHub Release only after deployment and smoke testing succeed,
   using the verified tag, image, provenance, and deployment evidence.

No storage, governance, audit, connector, or configuration migration is
introduced.

## Rollback

For an upstream-only rollback, restore the exact reviewed 7.14.1 image and wait
for periodic compatibility reconciliation or restart Engineering to force
immediate rediscovery. Verify the selected 7.14.1 entry, 26 reads, dashboard
status, governance and audit persistence, and zero fallback.

For an Engineering rollback, reinstall the exact published 2.0.0 image:

```text
ghcr.io/jeter-1/hass-mcp-engineering-beta@sha256:d91246deab5b50749430f5194b5a9fe1473171526fe4f8551c89b1b3259ff130
```

Retain `/data`, options, secrets, and the connector endpoint. Verify version
2.0.0 and source SHA
`496006b77039a42d7a8c8f23c0bbb292f5f0ddcd`, then repeat foundation,
admission, governance, audit, and no-fallback checks.

Stable v1.1.2 remains operationally retired historical source. It is unchanged
by this promotion and is not a supported Engineering rollback installation.

## Known limitations

- Reviewed-version reconciliation may take up to approximately 15 minutes
  without an Engineering restart; delegated reads fail closed and never fall
  back during that interval.
- A missing `ha_get_entity` registry entry can still produce bounded generic
  `provider_error` / `upstream_error` / `retryable=true` because both reviewed
  releases use ambiguous structured `SERVICE_CALL_FAILED`.
- Runtime source and image values are reviewed evidence; the Engineering MCP
  process does not independently observe the running upstream artifact.
- Broad automation configuration-body search can remain slow when upstream
  bulk access is unavailable, and bounded search can return explicit
  non-exhaustive partial coverage.

Immediate call-triggered reconciliation, a shorter reprobe interval,
`ha_get_entity` prose-based normalization, issue #62 enforcement, additional
upstream releases, dashboard administration, approval-notification changes,
principal separation, dependency-index performance work, and 2.1.0 features
are not included.

## Explicit non-actions

Preparing this promotion does not access or change a live Home Assistant,
upstream MCP server, connector, repository setting, tag, GitHub release, image,
attestation, deployment, or add-on process. It does not publish, merge, restart,
or begin 2.1.0 work.
