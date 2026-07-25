# 2.0.1-rc1-dev1 acceptance contract

Version: `2.0.1-rc1-dev1`

Status: development-candidate verification procedure only; not published,
deployed, or accepted

This is repository and disposable-environment review guidance. Publication,
deployment, release tagging, and live Home Assistant access remain outside its
scope.

## Repository and dependency gates

1. Require source ancestry from accepted 2.0.0 commit
   `496006b77039a42d7a8c8f23c0bbb292f5f0ddcd`.
2. Confirm historical stable v1.1.2 files are byte-for-byte unchanged and its
   behavioral tests are excluded from the Engineering dependency environment.
3. Confirm all production `_tool_manager` and private tool-map access is
   isolated in `mcp_sdk_compatibility.py`.
4. Exercise supported, missing-manager, missing-map, wrong-map, invalid
   replacement, transactional restoration, cached version validation,
   read-only mapping snapshot, and exact-removal adapter cases.
5. Require installed MCP SDK version 1.28.1 and fail closed for any other
   version or incompatible registry shape.
6. Confirm outbound Engineering initialization requests reviewed protocol
   `2025-03-26`, while a different returned protocol still fails exact
   admission closed. Compare normal and reviewed sessions and require the same
   returned server information, server capabilities, public
   `get_server_capabilities()` result, and initialized notification.
7. Confirm delegated tools support MCP 1.28 result conversion without changing
   their bounded JSON text content or reviewed output contract.
8. Resolve only the exact Engineering runtime requirements and run pinned
   pip-audit in strict mode with no ignored advisory. Do not describe this as
   retired stable-v1 dependency assurance.
9. Run the complete Python, compilation, metadata, YAML, dependency,
   secret-scan, PowerShell, protected-path, whitespace, and Evidence gates.
10. Build the historical stable-v1 image as packaging evidence only, build the
    Engineering image, and validate Engineering no-push builds for amd64,
    arm64, and arm/v7.
11. Confirm production FastMCP remains bound to `0.0.0.0` without an explicit
    Host or Origin policy. Treat CVE-2025-66416 as a mitigated, deferred
    configuration risk tracked by issue #62, not as fixed by the SDK upgrade.

## Contract comparison

Require unchanged evidence for:

- 41 Engineering tools plus 26 delegated reads, for 67 total;
- the exact reviewed 78-tool `ha-mcp` 7.14.1 fixture and catalog fingerprint;
- input-schema, description, annotation, output-schema, and runtime-contract
  fingerprints;
- upstream policy classifications and exact admission;
- delegated validation, not-found, capability-unavailable, authentication,
  connection, timeout, internal-failure, and partial-search semantics;
- quarantine, collision, unknown-tool, and all mismatch handling;
- dashboard constrained-read attestation;
- dependency indexing, governance, approval, apply verification, and rollback;
- audit, redaction, health accounting, provider attribution, and zero fallback;
  and
- unchanged historical stable v1.1.2 source and packaging, without claiming
  dependency-faithful behavioral validation or supported rollback.

Do not update expected fingerprints merely to pass validation.

## Pinned-image and disposable acceptance

The exact-image gate must build the upgraded Engineering image while keeping
the exact reviewed `ha-mcp` 7.14.1 image. Exercise normal delegation, exact
admission, invalid request, missing entity, missing automation, unavailable
capability, partial search, quarantine, every reviewed fingerprint mismatch,
unknown upstream tool, unavailable/recovery behavior, timeout, malformed
Streamable HTTP traffic, and session/transport failure. Require process
continuity, bounded failures, truthful counters, and zero fallback. Do not
claim production Host or Origin rejection: the exact-image job does not send
or verify reviewed production Host values.

The disposable Home Assistant contract tests may use only their pinned,
ephemeral test environment. They must not access a household or deployed Home
Assistant system.

## Separately authorized runtime review

After eventual publication and deployment, an independent reviewer should:

1. Verify version `2.0.1-rc1-dev1`, exact build SHA, and clean build.
2. Confirm Home Assistant connectivity and 67 tools: 41 Engineering plus 26
   delegated.
3. Confirm exact `ha-mcp` 7.14.1 admission, 78 advertised tools, the reviewed
   catalog fingerprint, zero mismatch, and zero quarantine.
4. Exercise normal delegated reads, validation and domain errors, bounded
   partial search, dashboard reads, and dependency prewarm.
5. Verify governance plans and audit history remain readable.
6. Verify provider, health, audit, redaction, and zero-fallback accounting.
7. Send malformed and transport-failure traffic only through an approved safe
   harness and confirm the server remains running.
8. Confirm documentation does not claim production DNS-rebinding protection.
9. If specifically authorized, roll back to the exact published 2.0.0
   Engineering image
   `ghcr.io/jeter-1/hass-mcp-engineering-beta@sha256:d91246deab5b50749430f5194b5a9fe1473171526fe4f8551c89b1b3259ff130`
   from source SHA
   `496006b77039a42d7a8c8f23c0bbb292f5f0ddcd`, and verify version, SHA,
   persistence, delegated reads, governance, and fallback.
