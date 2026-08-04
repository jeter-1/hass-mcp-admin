# HA MCP Engineering Server 2.2.0-beta.15 acceptance

## Source boundary

- required base: `4d2554930a680fc20b2a5beffaf7102e8c6bcb65`
- Engineering version: `2.2.0-beta.15`
- exact upstream protocol: `2025-03-26`
- secure pins: `aiohttp==3.14.3`, `cryptography==50.0.0`
- stable source: `1.1.2` unchanged
- Engineering-local tools: 48
- task schema: 1
- approval authority: 3
- fallback: 0

Record the reviewed PR head, merge SHA, immutable published image, and deployed
build identity separately. Source validation must not access production Home
Assistant, HAOS, Supervisor, credentials, add-on options, or logs.

## Required source and CI validation

1. Reproduce Beta 14's rejection of the source-derived 71,986-byte minimum
   8.0.0 detail text as `invalid_response`, before plan persistence and with
   zero dispatch and zero fallback.
2. Require exact 8.0.0 and protocol `2025-03-26` to select
   `ha-mcp-lifecycle-addon-structured-content-v1` and
   `mcp-direct-structured-content-v1`. Exact 7.14.2 must retain its reviewed
   bounded text model. Unknown release, protocol, or model must fail closed.
3. Require one valid text item and matching direct structured content under
   explicit global and model-specific bounds. Reject malformed, missing,
   divergent, failed, partial, truncated, or warned envelopes without exposing
   their contents.
4. Require complete installed inventory, exactly one exact slug, safe identity
   types, and agreement across list/detail slug, name, version, state, and the
   repository identifier when present. A present repository must equal the
   exact prefix of the installed slug; JSON null and omission both normalize
   to projected `None`. Endpoint host must remain the exact full-slug
   Supervisor DNS transform. Reject zero or duplicate matches and every
   identity mismatch before plan creation.
5. Require the response projector to retain only slug, name, version, state,
   optional repository, and optional Boolean update availability. A response
   may omit either optional field or provide JSON null; after exact
   parallel-envelope agreement, both normalize to projected `None`. Every
   present repository is exact-prefix validated and every non-Boolean,
   non-null update value fails closed. No options,
   translations, arbitrary fields, unrelated add-on identities, exception
   strings, tokens, or credential-bearing URLs may enter plans or health.
6. Run the immutable 8.0.0 amd64 add-on index
   `sha256:693ecd5c68f98e64111fbf58e02547a51b2168a942056684dbe262c550aff9cd`
   and manifest
   `sha256:65856752c37e4c1f9093060fbbc4a1a826cac1cbd6a76e856af5f5672a96c404`
   against the live-equivalent Supervisor fixture. Require the fixture to
   report profile `live-8.0.0` and the conservative 71,986-byte source-derived
   minimum. That bound is reproducible without production data from the seven
   `homeassistant-addon/translations/*.yaml` files at exact upstream commit
   `9dd3ac620e3149cd34ec3c990b6ee81e778191f2`: their parsed compact JSON is
   71,848 bytes, and the exact success/add-on wrapper plus slug, name, version,
   and state raises the minimum to 71,986 bytes. Repository, update status, and
   other real Supervisor detail fields only increase it. The fixture uses
   synthetic filler of that cardinality; it does not copy live options or
   configuration.
7. Require automatic reads, Dashboard list/configuration, backup planning,
   controlled reload planning, add-on restart planning, and Home Assistant
   restart planning. Exactly four disposable proposals may persist; provider
   dispatch, fixture HTTP/WebSocket mutation, backup creation, and fallback
   must all remain zero.
8. Run syntax, YAML, dependency consistency, fresh `pip check`, strict
   `pip-audit`, focused lifecycle/identity negatives, exact 7.14.2 and 8.0.0
   matrices, automatic-read/Dashboard/backup regressions, Fast, Full, Evidence,
   packaging, amd64/arm64/arm-v7 validation, exact-image 7.14.1/7.14.2/8.0.0,
   real-HA contracts, secret scan, protected-path checks, whitespace, and
   PowerShell parsing. Unexpected skips are failures until explained.

## Exact 7.14.2 regression

Require exact entry `ha-mcp-v7.14.2-7917b2d3`, 78 advertised tools, 26
delegated reads, zero held reads, 48 local tools, and 74 total tools. Require
zero schema, description, annotation, output, runtime, quarantine, missing,
unreviewed, or fallback counts. Dashboard inventory/configuration, backup
planning, controlled-reload planning, exact add-on-restart planning, and Home
Assistant-restart planning must succeed without dispatch.

## Later exact 8.0.0 live canary

The later canary requires Engineering version `2.2.0-beta.15`, its exact clean
published build identity, entry `ha-mcp-v8.0.0-d65630f6`, protocol
`2025-03-26`, 78 advertised tools, 24 delegated reads, exactly two held reads,
48 local tools, and 72 total tools. The held set must remain exactly
`ha_search` and `ha_get_operation_status`; both must remain unregistered and
non-callable. Require zero mismatch, quarantine, missing, unreviewed, and
fallback counts.

Reconfirm representative reads, Dashboard inventory and exact configuration,
backup planning, controlled-reload planning, exact add-on-restart planning,
and Home Assistant-restart planning. Planning-only probes create durable
proposals and therefore require explicit operator authorization. No proposal
may be approved or applied during the canary unless separately authorized.
Require exact response model/envelope health, one bound upstream add-on
identity, zero dispatch, zero Home Assistant mutation, and zero fallback.

## Rollback and stop boundary

Restore exact `ha-mcp` 7.14.2 if the response model or build identity differs;
tool counts or the held set change; any catalog/descriptor mismatch,
quarantine, missing, unreviewed, or fallback count appears; Dashboard or backup
regresses; lifecycle planning rejects the exact live response; any duplicate or
ambiguous identity appears; any plan dispatches; or Engineering becomes
unstable. If Beta 15 itself regresses while 8.0.0 is installed, restore the
previous secure Engineering build and exact 7.14.2 under a separately
authorized operator procedure.

This document authorizes neither deployment nor live access. Production
acceptance remains pending independent review, publication, deployment, and a
separately authorized canary.
