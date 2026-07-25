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
- PyYAML 6.0.2 and jsonschema 4.25.1 remain pinned because the HA MCP
  Engineering dependency audit reports no applicable finding for those direct
  pins.
- Private FastMCP registry access is isolated behind one compatibility adapter.
  Startup admits only MCP 1.28.1 with the reviewed tool-map shape and fails
  closed with bounded wording if that contract changes.
- Pull-request CI now runs pinned pip-audit against the Engineering runtime
  requirements. The same reusable CI job must pass before release promotion.
  No vulnerability exception is configured.

MCP 1.28.1 moves the runtime beyond the affected package-version ranges for
CVE-2025-53366, CVE-2025-53365, CVE-2026-52869, and CVE-2026-59950. The
patched web-stack versions also address the applicable public-advisory
findings reported for the former direct pins.

CVE-2025-66416 remains a reviewed, mitigated, deferred configuration risk.
MCP 1.28.1 contains transport-security controls, but FastMCP automatically
configures its loopback policy only for loopback binds. Engineering constructs
the production server with `host="0.0.0.0"` and no explicit Host or Origin
allowlist. Secret-path authentication, complete authenticated-path audit
redaction, access-log suppression, a 24-character minimum secret, and
authentication/general rate limiting remain mitigations. Valid production
Host and Origin values are deployment-specific, so this PR does not guess
them. Configurable production enforcement is deferred to issue #62.

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
so the existing no-redirect client contract is preserved. The loopback-only
SDK Host rejection fixture is retained as SDK behavior evidence, not as a
claim about the production all-interfaces bind. Outbound Engineering sessions
explicitly request reviewed protocol
`2025-03-26`; MCP 1.28's newer default is not used to broaden the exact
`ha-mcp` 7.14.1 admission contract. Delegated tools implement MCP 1.28's
explicit result-conversion callback while retaining the existing bounded JSON
text content and reviewed output contract.

Stable v1.1.2 source and packaging remain unchanged as historical repository
material. The add-on is operationally retired, is not covered by the
Engineering 2.0.1 dependency audit, is not a functional equivalent of v2, and
is not a supported rollback target.

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
published 2.0.0 Engineering image
`ghcr.io/jeter-1/hass-mcp-engineering-beta@sha256:d91246deab5b50749430f5194b5a9fe1473171526fe4f8551c89b1b3259ff130`,
built from source SHA
`496006b77039a42d7a8c8f23c0bbb292f5f0ddcd`. Preserve the existing v2 add-on
identity and `/data`, verify version, source SHA, and health afterward, and
rerun foundation, delegated-read, governance-persistence, and zero-fallback
checks. No migration is introduced by this candidate.
