# HA MCP Engineering Server 2.2.0-beta.15 release notes

## Release boundary

Beta 15 is a narrow lifecycle response-compatibility correction for exact
`ha-mcp` 8.0.0 under protocol `2025-03-26`. Beta 14 already corrected
automatic-read, Dashboard v3, backup, controlled-reload, and full-catalog
admission. Its live canary then reached the exact add-on inventory and detail
read used to bind add-on-restart identity. Both planning requests failed closed
as `invalid_response` before plan persistence or dispatch (request IDs
`4d5814ad0b364a708b61fb7d0fdbeaf3` and
`9adbd6428b774f3c808bbce953d25139`).

Beta 15 changes no automatic-read classification, Dashboard or backup
contract, provider action, approval policy, dispatch argument, protocol,
release trust, held-tool decision, or fallback. It preserves exact 7.14.2 and
8.0.0 catalog admission, `ha-mcp-reviewed-normalized-catalog-v1`, the Beta 13
pins `aiohttp==3.14.3` and `cryptography==50.0.0`, strict dependency auditing,
and stable v1.1.2.

## Exact rejected response condition

Exact `ha-mcp` 8.0.0 projects installed add-on inventory to a compact list but
passes the Supervisor `/addons/{slug}/info` mapping through for an exact-slug
read. Supervisor 2026.7.4 includes the add-on's full translation mapping in
that detail response. The reviewed 8.0.0 add-on contains seven public
translation documents. Translations plus the required lifecycle identity
fields produce a compact FastMCP response of at least 71,986 bytes, while Beta
14 applied the generic 60,000-byte lifecycle text limit to the redundant
`content[0].text` member.

The same MCP result also contains the exact dictionary in direct
`structuredContent`, and the transport already bounds the complete exchange at
one megabyte. Beta 14 ignored that reviewed structured envelope. Its synthetic
Supervisor fixture returned the compact inventory record for both list and
detail reads, so the immutable add-on runtime test never crossed the stale
text limit.

## Exact lifecycle response contracts

Lifecycle response decoding is now selected only after exact release, version,
and protocol admission. Reviewed 7.14.1 and 7.14.2 retain the bounded
`ha-mcp-lifecycle-addon-text-json-v1` model. Exact 8.0.0 selects
`ha-mcp-lifecycle-addon-structured-content-v1` with envelope
`mcp-direct-structured-content-v1`.

The 8.0.0 model retains the transport-wide one-megabyte cap and adds a bounded
250,000-byte parallel-text cap. It requires one text content item, direct
structured content, matching text and structured payloads, `success=true`, a
complete installed inventory summary, exactly one requested slug, and exact
agreement between inventory and detail identity. It immediately projects only
slug, name, installed version, state, optional repository, and the optional
Boolean update flag. A present repository must equal the installed slug's exact
repository prefix. After exact agreement between the two MCP envelope surfaces,
an omitted or JSON-null optional field normalizes to projected `None`; any
non-null invalid repository or non-Boolean update value is rejected.
Translations, options, descriptions, arbitrary add-on fields, and unrelated
inventory identities are neither retained nor exposed.

Unknown release/protocol/model combinations, malformed envelopes, mismatched
parallel representations, incomplete or warned inventories, zero or duplicate
slug matches, invalid field types, and identity drift remain fail-closed before
plan creation. Endpoint-host and admitted-upstream identity binding remain
exact. Planning still cannot dispatch `start`, `stop`, `install`, `uninstall`,
`update`, options mutation, proxy operations, or arbitrary arguments; the only
reachable apply action remains `action=restart` for the exact planned slug
after separate approval.

## Live-equivalent immutable acceptance

The distinct immutable 8.0.0 add-on runtime job now uses a secret-free
Supervisor detail fixture with the source-derived envelope and a conservative
71,986-byte minimum response cardinality. The value is reproducible from the
seven public `homeassistant-addon/translations/*.yaml` files at exact upstream
commit `9dd3ac620e3149cd34ec3c990b6ee81e778191f2`: parsed compact translations are
71,848 bytes, and the minimum success/add-on wrapper plus slug, name, version,
and state is 71,986 bytes before repository or any other Supervisor fields.
The fixture uses synthetic filler rather than copying production data. The
older compact synthetic variant remains unit-regression coverage. The
immutable job must reproduce 78 advertised tools, 24 delegated
reads, the two held reads, Dashboard list/configuration, backup planning,
controlled-reload planning, exact add-on-restart planning, and Home Assistant
restart planning. It requires four disposable governed proposals, zero
provider dispatch, zero fixture mutation, and zero fallback.

Native add-on execution remains amd64-only in CI. Existing architecture jobs
verify arm64 and arm/v7 layers, source, configuration, metadata, and build
identity without claiming native runtime execution.

## Acceptance status

Exact 7.14.2 remains 78 advertised tools, 26 delegated reads, zero held reads,
48 Engineering-local tools, and 74 total tools. Exact 8.0.0 remains 78
advertised, 24 delegated, exactly two held, 48 local, and 72 total. The held
tools remain `ha_search` and `ha_get_operation_status`; neither is registered
or callable. Zero fallback remains mandatory.

Beta 15 source and offline acceptance do not establish production success.
Production `ha-mcp` 8.0.0 remains only partially accepted until Beta 15 is
independently reviewed, deployed, and tested through a separately authorized
live canary following
[`V2_2_0_BETA15_ACCEPTANCE.md`](V2_2_0_BETA15_ACCEPTANCE.md).
