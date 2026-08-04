# HA MCP Engineering Server 2.2.0-beta.14 acceptance

## Source boundary

- required base: `88f82e1a2b1994bdf2d548369c2fdf9657570fa9`
- Engineering version: `2.2.0-beta.14`
- inherited secure pins: `aiohttp==3.14.3`, `cryptography==50.0.0`
- stable source version: `1.1.2` (unchanged and operationally retired)
- Engineering-local tools: 48 (25 canonical plus 23 Engineering-native)
- task schema: 1
- approval authority: 3
- protocol: `2025-03-26`
- fallback: 0

Record the exact reviewed PR head and later merge/build identity separately.
Source validation must not access production Home Assistant, Engineering,
HAOS, Supervisor, credentials, or add-on options.

## Required source and CI validation

1. Require the shared full-catalog validator to run only after exact release
   and protocol selection, compare the exact tool-name set without duplicates,
   and account for every reviewed classification and descriptor component
   across all 78 tools.
2. Require exact 7.14.2 standalone and exact 8.0.0 standalone/add-on catalogs
   to validate. Require unknown 8.0.1 and 8.1.0, wrong protocol, unknown model,
   additional, missing, duplicate, unreviewed, malformed, or changed tools to
   fail before provider dispatch.
3. Require security-negative coverage for policy shape, keys, types,
   deployment and rule bounds plus changes to pinning, LLM exposure, tags,
   annotations, name, description, input schema, and output contract.
4. Require the aggregate model
   `ha-mcp-reviewed-normalized-catalog-v1` to reproduce
   `3bad86b86400807ceddf68805cf4ed86d1243f201104e18ed8d3c15e560a1d53`
   for the exact 8.0.0 add-on runtime while retaining its distinct raw catalog
   fingerprint as diagnostic evidence.
5. Require the reviewed Dashboard contract to accept exact 7.14.2 and
   Dashboard v3 to accept both reviewed 8.0.0 deployments while rejecting
   malformed policy metadata and any expanded screenshot, preference,
   rendering, path, service-call, or write surface. Require the v3 reviewed
   normalized runtime fingerprint to be
   `fb7f3789c8c020d8636a96b85a207635e94eefe9e0944c8814de59aba17e532e`.
6. Require recognized homogeneous and heterogeneous dashboard exception groups
   to preserve bounded typed categories; unknown-only groups must remain
   `internal_error` without exposing exception content.
7. Require backup and lifecycle planning for exact reviewed catalogs to create
   governed proposal records without dispatch. Catalog, required-tool, action,
   argument, or authoritative add-on-binding drift must fail before plan
   persistence.
8. Execute the immutable 8.0.0 amd64 add-on index
   `sha256:693ecd5c68f98e64111fbf58e02547a51b2168a942056684dbe262c550aff9cd`
   and verify platform manifest
   `sha256:65856752c37e4c1f9093060fbbc4a1a826cac1cbd6a76e856af5f5672a96c404`.
   Verify the arm64 index and manifest separately without claiming native
   execution.
9. The exact add-on runtime must reproduce 78 tools and raw fingerprint
   `c61b0959e766f3900300dd4dd69a6d799fc113186d91983f21be69f1bc6b8768`,
   admit 24 reads, hold exactly two, admit dashboard list/get, and create four
   planning-only proposals with zero backup/reload/restart dispatch, zero
   synthetic fixture mutation, and zero fallback.
10. Run compilation, YAML, dependency consistency, fresh `pip check`, strict
    `pip-audit`, focused positive and negative provider tests, Fast, Full,
    Evidence, packaging, exact standalone, exact add-on runtime, real-HA
    contract, secret-scan, and supported-architecture gates. Record unavailable
    local Docker or native architecture execution accurately and require the
    corresponding exact-head CI jobs instead.
11. Require a clean final worktree, unchanged stable source, the Beta 13 secure
    dependency pins, 48 local tools, task schema 1, authority version 3,
    protocol `2025-03-26`, exact-version trust only, and no fallback.

## Exact 7.14.2 baseline

Before any upstream update, deploy only the reviewed Beta 14 build while
leaving upstream on exact 7.14.2. Verify exact version/build/clean identity,
Home Assistant connectivity and configuration, 48 local plus 26 delegated
equals 74, exact entry `ha-mcp-v7.14.2-7917b2d3`, zero schema, description,
annotation, output, runtime, quarantine, missing, unreviewed, or fallback
counts, healthy governance and task storage, accepted dashboard list/get, and
planning-only backup and lifecycle contracts with zero dispatch.

This deployment is a separately authorized operator action. This document does
not authorize deployment or live access.

## Controlled exact 8.0.0 canary

Only after the 7.14.2 baseline and rollback readiness pass may a separately
authorized operator update upstream to exact 8.0.0. Require entry
`ha-mcp-v8.0.0-d65630f6`, protocol `2025-03-26`, 78 advertised tools, 24
exposed reads, two held reads, 48 Engineering-local tools, and 72 total tools.
The held set must remain exactly `ha_search` and
`ha_get_operation_status`; both must remain unregistered and non-callable.

Require zero schema, description, annotation, output, runtime, quarantine,
missing, unreviewed, and fallback counts. Run representative delegated reads,
dashboard inventory, exact canonical dashboard configuration read, backup
planning, controlled-reload planning, exact add-on-restart planning, and Home
Assistant-restart planning. Planning may persist only governed proposals and
must not dispatch a backup, reload, add-on restart, Home Assistant restart,
physical action, or fallback. Do not approve or apply those canary plans unless
a separate acceptance instruction explicitly authorizes the action.

## Rollback and stop conditions

Restore exact 7.14.2 if identity, protocol, tool totals, held set, catalog
validation, dashboard contract, backup/lifecycle planning, authoritative
add-on binding, storage, reconciliation, or zero-fallback invariants differ;
if an unexpected write or dispatch occurs; or if Engineering becomes unstable.
Stop without widening trust or removing a gate. Do not admit an unknown 8.x,
promote a held tool, broaden dashboard/provider arguments, or add fallback.

Production acceptance remains pending. This document records the later
operator boundary; it does not claim deployment or live Beta 14 success.
