# HA MCP Engineering Server 2.2.0-beta.21 release notes

Beta 21 performs a complete exact-release review of `ha-mcp` 8.1.0 and adds a
narrow compatibility entry for its reviewed immutable OCI runtimes. Beta 20
remains reserved for F3-D; this branch is based directly on merged Beta 19 main
`51943e11cc5290b1bf8db75474982193463044f5`.

The controlling runtime catalog remains 78 tools. Engineering admits 24 exact
automatic reads, holds exactly `ha_search` and
`ha_get_operation_status`, retains 48 local tools, and exposes 72 total tools.
Every advertised runtime tool is classified. Source-only, conditionally
registered, hidden, and nonadvertised names are recorded separately and never
treated as catalog authority.

The result is Outcome C because one consumed response changed. In 8.1.0,
`ha_get_hacs_info` moved `success` from `/data/success` to `/success`. An exact
release-, protocol-, and tool-bound projector validates the new envelope and
restores Engineering's stable nested-success result. It rejects ambiguous,
malformed, oversized, duplicate, non-finite, extra, missing, wrong-type, or
divergent representations rather than broadening the generic parser.

The related write tool, `ha_manage_hacs`, adds `action=remove`. Beta 21
reclassifies the entire tool as persistent write. No download, repository-add,
or remove route is registered; all actions remain unreachable until a separate
governed review.

Dashboard, backup, reload, add-on, and Home Assistant restart contracts remain
exact-release bound. Captured 8.0.0 and 8.1.0 add-on inventory/detail envelopes
are byte-identical, so lifecycle reuses the existing strict structured-content
model with an explicit 8.1.0 binding. Supervisor installed inventory remains
the authoritative installed-version source. Tests prove that the tagged
source tree's stale add-on `version: 8.0.0` cannot become the installed 8.1.0
identity or authorize restart planning.

Runtime evidence covers stable settings-sidecar identity, corrupt-state
regeneration, loopback-only binding, shutdown cleanup, pending embedded-worker
cancellation, forced Engineering disconnect, and exact no-fallback
readmission. Exact-image CI retains 7.14.1, 7.14.2, and 8.0.0 and adds the
immutable 8.1.0 standalone and add-on identities.

The 8.1.0 release-page executables and MCPB are explicitly excluded. Their
digests match the release page, but their embedded application identity is
8.0.0 because their workflow built a pre-tag parent. The immutable standalone
and add-on OCI runtimes report 8.1.0 and are bound by exact index/platform
digests; no mutable tag or release family is trusted.

Beta 21 preserves protocol `2025-03-26`, the
`ha-mcp-reviewed-normalized-catalog-v1` model, Dashboard v3 normalization,
provider argument constraints, planning/dispatch separation, zero fallback,
`aiohttp==3.14.3`, `cryptography==50.0.0`, stable v1.1.2, task/plan contracts,
governance, and all F3 runtime boundaries. Exact 7.14.2 and 8.0.0 remain
supported without inheriting trust across versions.

Production acceptance of `ha-mcp` 8.1.0 remains pending a separately
authorized controlled canary. This source release does not deploy, update an
upstream add-on, perform a provider operation, create a tag, publish an image,
or access production.
