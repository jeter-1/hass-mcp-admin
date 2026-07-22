# RC2dev13 acceptance

Version: `2.0.0-rc2-dev13`
Status: active staged procedure; acceptance has not yet passed

This document authorizes only the verification procedure for the exact
RC2dev13 candidate. It does not record a pass, authorize publication by itself,
or authorize deployment or access to a live Home Assistant system.

## Pre-promotion repository gates

1. Review the complete candidate diff from its exact `origin/main` base.
2. Confirm stable v1.1.2, all public MCP schemas, native tool registration,
   provider policy/trust data, governance and approval behavior, container
   files, and deployment operations are unchanged. Review the one narrow
   workflow-YAML change: `publish-rc-image.yml` must validate exact document
   authority for both staged and preversioned modes before publication.
   Confirm its triggers, permissions, action references, immutable-reference
   checks, generated release references, and publication semantics after that
   fail-closed prerequisite are unchanged.
3. Require focused recovery/completeness tests, the complete unittest suite,
   Python compilation, metadata validation, YAML parsing, dependency checks,
   secret scan, PowerShell syntax, protected-path scope, whitespace checks, and
   schema-v2 Evidence snapshot binding to pass from a clean committed head.
4. Require the exact-image read-gateway test to verify ha-mcp 7.14.1 identity,
   40 statically registered (25 canonical + 15 Engineering-native) + 26
   delegated tools, reviewed annotations, representative reads,
   no write exposure, no fallback, and truthful `ha_search` completeness.
5. Confirm `2.0.0-rc2-dev12 < 2.0.0-rc2-dev13 < 2.0.0-rc.3 < 2.0.0` with the
   repository-pinned AwesomeVersion implementation.
6. Require staged document resolution to be `exact`, with this file as the
   known staged acceptance document. Release notes are not acceptance
   instructions, and historical documents cannot authorize this candidate.

## Promotion identity and provenance

After a separately authorized merge, require the protected promotion to build
the exact release commit and publish new immutable `2.0.0-rc2-dev13` and
`sha-<release-sha>` references to one verified amd64, arm64, and arm/v7 index.
Require revision/version/creation labels, SLSA provenance, the Engineering SPDX
SBOM, annotated tag target verification, and `release_complete=true`. Record
the exact release SHA and image digest before any deployment decision.

RC2dev12's tag and digest must remain unchanged. A partial promotion is a stop
and reconciliation condition, never authority to reuse or overwrite an
existing artifact.

## Separately authorized beta deployment

Update only Engineering Beta to the exact recorded RC2dev13 digest. Keep stable
v1.1.2 and ha-mcp 7.14.1 unchanged. Do not change Home Assistant, GitHub
settings, secrets, environments, or any other add-on. Capture governance and
audit baselines before the reboot test. Before deployment, record and
anonymously verify one previously independently accepted Engineering rollback
version and digest. If none is known and available, stop; RC2dev12 is not an
eligible rollback.

## Full-host reboot recovery gate

1. Before reboot, require exact RC2dev13 server/build identity, 40 statically registered tools,
   26 reviewed delegated reads, zero delegated writes, and zero fallback.
2. Perform one separately authorized full Home Assistant host reboot. Do not
   manually restart Engineering or ha-mcp during the observation window.
3. Record add-on start ordering. The gate specifically covers Engineering
   becoming available before ha-mcp.
4. While upstream admission is unavailable, require the 40 statically registered tools to stay
   available and require gateway health to report bounded, secret-free retry
   state. No unadmitted delegated tool may appear.
5. For up to ten minutes after Engineering starts, poll health and issue fresh
   `tools/list` requests. Require exact ha-mcp 7.14.1 admission and automatic
   convergence to 66 tools without an Engineering restart. Record discovery
   attempts, normalized failure categories, retry count, admission time, and
   final catalog fingerprint.
6. Reconnect or explicitly re-list from any client that cached 40 tools. Do not
   claim a `tools/list_changed` notification; RC2dev13 does not advertise or
   broadcast one.
7. Call representative delegated reads and require automatic recovery, correct
   provider identity/version, no fallback, and no write exposure.
8. Interrupt ha-mcp in a controlled, separately authorized test. Require safe
   delegated-call failure and automatic call recovery after ha-mcp returns,
   without loss of native tools, governance records, or audit records.

Failure to reach the exact 66-tool catalog within the bounded window, any need
for a manual Engineering restart, admission of a mismatched catalog, or loss of
native service fails the full-host reboot gate.

## `ha_search` semantic-completeness gate

1. Execute a bounded search that ha-mcp reports with top-level
   `partial: true`. Require Engineering data to retain that boolean and require
   `metadata.completeness: partial`, partial provider accounting, and partial
   request/audit telemetry.
2. Execute a bounded search with exact `partial: false`. Require complete
   metadata unless Engineering's local sanitizer bounded the response.
3. Confirm fixed local warnings contain no copied secret or untrusted upstream
   instruction. Confirm redaction and response-size failure behavior remains
   fail closed.
4. Reconcile `get_audit_log` and provider metrics with both calls. A successful
   partial result is not a provider failure, but it must never be reported as
   complete.

## Negative reachability and persistence

Require 40 statically registered + 26 delegated reads only. Confirm service execution, entity
or device mutation, dashboard mutation, preference persistence, arbitrary tool
forwarding, and direct-HA fallback remain unreachable before, during, and after
recovery. Confirm governance state, audit state, and dependency-index behavior
persist across the full-host reboot.

## Pass, fail, and rollback record

Acceptance passes only when every applicable step above is tied to the exact
RC2dev13 release SHA and digest and the reboot and semantic-completeness gates
both pass. Record unavailable evidence as unavailable; do not waive or infer it.

On any failure, stop. Preserve the failed RC2dev13 evidence and use only the
previously recorded, independently accepted, anonymously verified Engineering
rollback digest. Never retag an artifact, never use failed RC2dev12 as rollback, and do not
change stable v1.1.2 or ha-mcp unless separately authorized for an independent
incident.
