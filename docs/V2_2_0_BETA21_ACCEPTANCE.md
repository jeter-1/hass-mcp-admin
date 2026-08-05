# HA MCP Engineering Server 2.2.0-beta.21 acceptance

## Source and release boundary

- direct base: merged Beta 20 main
  `e2152911c0f3581c38b6ef42e52a2dd221cd8d96` with tree
  `7236119b1aeb975f6b13a477572d84fbabedb3ab`;
- Engineering version: `2.2.0-beta.21`, following the merged F3-D Beta 20
  release;
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

## Beta 20 restack evidence

PR #94 was originally prepared on merged Beta 19
`51943e11cc5290b1bf8db75474982193463044f5` at exact head
`b0ce98bc376dde2f7764616d7cee2e342719b881` and tree
`ccdec8b2f7820bc1d259f459ca5d70f5b89d0a25`. After PR #93 merged through the
protected merge-commit path, its exact Beta 20 merge identity was verified as
`e2152911c0f3581c38b6ef42e52a2dd221cd8d96`, with first parent
`51943e11cc5290b1bf8db75474982193463044f5`, second parent
`8b2c9231b9037862debcd6b4b3506830941891df`, and a tree exactly equal to the
accepted PR #93 head tree.

The eight Beta 21 commits were rebased directly onto that merge. The
implementation head before this evidence-only documentation commit is
`b89ccbbf22ef2bb9283965b8f25c7da4248af968`, with tree
`f8e1306fe07734aa23958d3a0d5f58a6440fa330`. Seven commits retained identical
patch IDs. The release declaration commit changed only to integrate the merged
Beta 20 release notes, version lineage, and F3 import-boundary allowlist while
preserving Beta 21 metadata. The changed-path set is identical to the original
eight-commit range. The local recovery reference
`refs/backup/pr94-pre-beta20-restack-20260805T204314Z` preserves the original
head and was not pushed.

The restacked local Full gate passed 1,916 tests with two expected skips and
zero failures. Focused validation passed 47 F3 runtime tests, 31
compatibility/registry tests, 17 exact-runtime tests, 23 HACS/catalog tests,
and 129 lifecycle/provider regression tests. Registry generation was
byte-identical across two runs and matched the checked-in registry at SHA-256
`163582e160398892ef8541e9f0c7e97de2b4e8c25301dcc867e0067a8a617035`.
Fresh dependency checking passed, and strict audit reported no known
vulnerabilities. Exact-head Evidence and CI remain release gates after this
document is committed.

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
