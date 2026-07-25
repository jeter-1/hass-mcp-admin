# 2.0.1-rc1-dev1 release notes

Status: unpublished 2.0.1 development candidate

This candidate is a bounded security and dependency hardening update to the
accepted 2.0.0 runtime. It adds no MCP tool or feature and records no
publication, deployment, or access to a live Home Assistant system.

## Changes

- MCP SDK: 1.9.0 to 1.28.1, the newest compatible stable 1.x release reviewed
  for this candidate.
- Direct Engineering dependencies:
  - aiohttp 3.9.5 to 3.14.2;
  - uvicorn 0.29.0 to 0.51.0, satisfying the MCP SDK's newer minimum;
  - Starlette 0.37.2 to 1.3.1; and
  - cryptography 45.0.7 to 48.0.1.
- PyYAML 6.0.2 and jsonschema 4.25.1 remain pinned because the dependency audit
  reports no applicable finding for those direct pins.
- Private FastMCP registry access is isolated behind one compatibility adapter.
  Startup admits only MCP 1.28.1 with the reviewed tool-map shape and fails
  closed with bounded wording if that contract changes.
- Pull-request CI now runs pinned pip-audit against the Engineering runtime
  requirements. The same reusable CI job must pass before release promotion.
  No vulnerability exception is configured.

MCP 1.28.1 moves the runtime beyond the affected ranges for
CVE-2025-53366, CVE-2025-53365, CVE-2025-66416, CVE-2026-52869, and
CVE-2026-59950. The patched web-stack versions also clear the applicable
public-advisory findings reported for the former direct pins.

## Compatibility

The candidate preserves 41 Engineering tools and 26 exact reviewed delegated
reads for 67 tools after exact `ha-mcp` 7.14.1 admission. The 78-tool reviewed
upstream fixture, catalog and per-tool fingerprints, schemas, descriptions,
annotations, output contracts, policy classifications, dashboard attestation,
governance, audit, partial-result handling, and zero-fallback policy are
unchanged.

The newer SDK handles fractional timeout values correctly. Engineering retains
its existing one-second minimum as an explicit local transport policy.
Endpoint-bearing SDK transport logs remain suppressed, and bounded session and
transport failure normalization remains in place. Both accepted external MCP
path forms are normalized to MCP 1.28's exact internal Streamable HTTP route,
so the existing no-redirect client contract is preserved. MCP 1.28's
DNS-rebinding protection remains enabled: a configured loopback server accepts
its loopback Host identity and rejects an untrusted Host before tool dispatch.
Outbound Engineering sessions explicitly request reviewed protocol
`2025-03-26`; MCP 1.28's newer default is not used to broaden the exact
`ha-mcp` 7.14.1 admission contract. Delegated tools implement MCP 1.28's
explicit result-conversion callback while retaining the existing bounded JSON
text content and reviewed output contract.

Stable v1.1.2 and its dependency manifest are unchanged.

## Limitations and rollback

The repository exact-pins direct dependencies but does not commit a hashed
transitive lock. pip-audit is point-in-time evidence based on its available
public advisory data.

The upgraded in-process test stack reports two upstream warnings that are not
suppressed: Starlette deprecates its HTTPX 0.x `TestClient` integration in
favor of the separately packaged HTTPX 2 client, and MCP 1.28.1 can report an
unclosed AnyIO memory receive stream while stateless synthetic requests are
being torn down. The production runtime does not use `TestClient`; all
continuity, malformed-request, transport-failure, and full-suite assertions
still pass. These warnings remain visible for upstream follow-up rather than
being hidden by this maintenance change.

Rollback, if separately authorized, is an in-place reinstall of the exact
accepted 2.0.0 Engineering image. Preserve the existing add-on identity and
`/data`, verify version and source SHA afterward, and rerun foundation,
delegated-read, governance-persistence, and zero-fallback checks. No migration
is introduced by this candidate.
