# HA MCP Engineering Server 2.2.0-beta.21 acceptance

## Source and release boundary

- direct base: merged Beta 19 main
  `51943e11cc5290b1bf8db75474982193463044f5`;
- Engineering version: `2.2.0-beta.21`; Beta 20 remains reserved for F3-D;
- stable version: `1.1.2`;
- secure pins: `aiohttp==3.14.3` and `cryptography==50.0.0`;
- exact upstream release: `homeassistant-ai/ha-mcp` 8.1.0 at tag target
  `0683f5ff34e5c71f35bce08d1cedcdee3c0a60b2`;
- protocol: `2025-03-26`;
- reviewed runtime scope: the immutable standalone OCI and Home Assistant
  add-on OCI identities in
  [the exact 8.1.0 review](evidence/upstream-read-compatibility/ha-mcp-8.1.0-review.md);
- compatibility outcome: C; and
- fallback: 0.

The GitHub release executables and MCPB are excluded because their embedded
application identity is 8.0.0. A mutable version tag, release-page membership,
or semantic-version relationship is not admission authority.

## Required exact 8.1.0 contract

Acceptance requires all of the following:

1. MCP initialize reports exact server name `ha-mcp`, version `8.1.0`, and
   protocol `2025-03-26`; unknown 8.1.x and later releases fail closed.
2. Exact runtime `tools/list` is the controlling catalog and contains 78 unique
   advertised names. All 78 are classified: 24 automatic reads, two held, 13
   mixed/wrapper-required, 33 persistent writes, four physical/high-risk
   actions, one prohibited tool, and one unsupported tool.
3. Held tools remain exactly `ha_search` and
   `ha_get_operation_status`; neither is registered or callable.
4. All per-tool name, description, schema, annotation/security, output, and
   release-model runtime components match. Standalone and add-on raw catalog
   hashes remain diagnostic; the exact model-aware normalized catalog gate is
   authoritative.
5. `ha_manage_hacs` is classified in its entirety as a persistent write after
   8.1.0 adds `action=remove`. Download, repository addition, and removal are
   all unreachable through Engineering without a later governed review.
6. `ha_get_hacs_info` uses the exact
   `ha-mcp-hacs-info-top-level-success-v1` response model only for 8.1.0. The
   model restores the reviewed nested-success representation while rejecting
   malformed, duplicate, non-finite, oversized, extra, missing, wrong-type, or
   divergent text/structured envelopes.
7. Dashboard v3, backup, reload, add-on inventory/action, and Home Assistant
   restart descriptors retain their exact argument and response contracts.
   Lifecycle inventory/detail reuses
   `ha-mcp-lifecycle-addon-structured-content-v1`; no broader response model is
   permitted because the captured 8.0.0 and 8.1.0 structures are identical.
8. Supervisor installed-add-on inventory is the installed-version authority.
   Its exact endpoint-bound version must equal the admitted runtime version.
   The tagged tree's stale add-on `version: 8.0.0` can never supply or override
   installed identity.
9. Exact-image lifecycle evidence proves stable sidecar identity across
   restarts, corrupt-state regeneration, loopback-only binding, restrictive
   state permissions, shutdown cleanup, pending-worker cancellation,
   Engineering disconnect, and exact no-fallback readmission.
10. Exact-image 8.1.0 acceptance exercises the complete 24-tool delegated set,
    failure/malformed paths, and held-tool non-callability. Immutable add-on
    acceptance exercises Dashboard reads and planning-only backup/reload/add-on
    restart/Home Assistant restart contracts with zero provider mutation.

## Regression contract

Exact 7.14.2 remains 78 advertised, 26 delegated, zero held, 48 local, and 74
total. Exact 8.0.0 remains 78 advertised, 24 delegated, two held, 48 local, and
72 total. Exact 8.1.0 must be 78 advertised, 24 delegated, two held, 48 local,
and 72 total. Every release must report zero schema, description, annotation,
output, runtime, quarantine, missing, unreviewed, and fallback counts for its
reviewed successful profile.

The source-only and conditionally registered tool names are diagnostic and do
not enter runtime accounting. The exact runtime catalog must have no duplicate,
additional, missing, or unclassified advertised tool.

## Validation and controlled-canary boundary

Require deterministic registry generation twice with a byte-identical result,
focused registry/provider/HACS/lifecycle/security tests, exact 7.14.2 and 8.0.0
regressions, exact standalone and add-on runtime acceptance, compilation, YAML,
Fast, Full, clean exact-head Evidence, fresh `pip check`, strict audit with no
known vulnerabilities, secret/protected-path/PowerShell/whitespace gates,
stable and Engineering packaging, architecture validation, disposable pinned
Home Assistant contracts, publication guards, and fully green exact-head CI.
Every skip requires an explicit explanation.

Production admission remains pending a separate controlled canary. That later
task must verify immutable deployed identity, 78/24/2/48/72 accounting, HACS
read normalization, Dashboard reads, planning-only special providers, exact
installed-version binding, zero dispatch, zero mutation, and zero fallback.
This document authorizes no merge, tag, release, image, deployment, upstream
update, production access, provider apply, or live canary.
