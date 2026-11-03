# HAMCP-251: ha-mcp 9.x upstream review — Part A

**Decision support, not admission.** Pin **ha-mcp 9.0.0**, source commit
`948536dfba941ae058c8b633767918d4b6df2497`. Protocol verdict: **compatible at
source level for Engineering's existing `2025-03-26` session**; exact-image
negotiation remains unobserved. Adoption is **wait**, pending architect review,
owner decisions on the carried-forward component/discovery risks, release-specific
Engineering changes and Part B. No runtime, policy, registry, version, signing or
live-system change is part of this report. [Pinned release](https://github.com/homeassistant-ai/ha-mcp/releases/tag/v9.0.0), [U:src/ha_mcp/_vendor/mcp/server/runner.py:421](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/_vendor/mcp/server/runner.py#L421), [E:hass_mcp_engineering_beta/ha_mcp_engineering/mcp_sdk_compatibility.py:177](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/mcp_sdk_compatibility.py#L177).

## Evidence boundary and baseline

- **Fact — repository:** base/initial HEAD `f8242b6e82064a5efb5cd0076ff60b4626d5b589`, branch `agent/hamcp-251-review`, isolated checkout `/tmp/hamcp251-review`, clean at intake. Engineering `2.4.1-beta.58`; retired/frozen stable `1.1.2`; no staged declaration. Verified with `git fetch --prune origin`, `git rev-parse` and `python3 scripts/codex-context.py --format markdown`. [E:hass_mcp_engineering_beta/config.yaml:2](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/config.yaml#L2), [E:hass_mcp_admin/retired-addon-config.yaml:7](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_admin/retired-addon-config.yaml#L7).
- **Owner/architect-reported, not reobserved:** production ha-mcp app `81f33d0f_ha_mcp` is 8.5.0, `admitted_exact`, auto-update OFF; component 2.2.1 is the HAMCP-187 owner decision. The October 29 beta.48 observation showed update available without a target, proxy app `81f33d0f_ha_mcp_webhook_proxy` 3.0.4 without an update, and supported versions through 8.6.0. Source: HAMCP-251/HAMCP-187 register rows read November 2, plus [E:docs/HAMCP_187_HA_MCP_870_REVIEW.md:1386](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/HAMCP_187_HA_MCP_870_REVIEW.md#L1386). These do not prove today's deployed configuration.
- **Fact — authority:** Josh's November 2 direction starts Part A now, superseding the earlier wait for Core for this part only. Part B requires publication of Core 2026.11.0. This is a documentation review with an unmerged draft PR; the architect reviews and Josh decides adoption. Source: task instruction and HAMCP-251 register row; [E:docs/architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md#L1).
- **Method:** exact public source checkouts of 8.5.0, 8.7.0 and 9.0.0; file/function/parameter comparisons; Engineering bindings read at the base above. The adjacent [source contract table](source-contract-table.md) supplies **every parameter, default, constraint, return annotation and exact source location** for 39 current tool names plus two historical lifecycle names. It is a raw source diff, not a generated or admitted runtime catalog. Upstream code is MIT licensed, [U:LICENSE:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/LICENSE#L1).
- **Unknown:** 9.0.0 OCI index/architecture/add-on digests, complete runtime catalog/fingerprints under the actual settings/component pairing, runtime errors, deployed negotiation and Nabu Casa end-to-end behavior. No upstream app/server or Home Assistant was installed, updated, started or accessed to fill these gaps. ADR-021 requires separate exact-image/disposable evidence before admission; source equality does not supply it. [E:docs/architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md:78](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md#L78).

Source links labeled `U` resolve to the pinned 9.0.0 commit; `S` to the
8.5.0 source; `E` to the exact Engineering base. Classification is an assessment
of the reviewed contract, not an authorization. **Facts** are source/API results;
**inferences** are expected integration effects; **unknowns** are explicitly named.

## 1. Version pin

**Fact:** all stable version tags after 8.5.0 visible in the complete upstream
`git ls-remote --tags` query were the following. Publication timestamps are from
GitHub's releases API, not inferred from tagger/commit dates. Annotated tag object
and peeled source commit are deliberately separate.

| Release | Published UTC | Annotated tag object SHA | Peeled source SHA |
|---|---|---|---|
| [8.5.0 baseline](https://github.com/homeassistant-ai/ha-mcp/releases/tag/v8.5.0) | 2026-09-16 14:38:43 | `f54c741d8e98d1cbdeff137dfc4851e0009506a2` | `311d6dc273fb4e9a5b8cde0de15f69472a64fe44` |
| [8.6.0](https://github.com/homeassistant-ai/ha-mcp/releases/tag/v8.6.0) | 2026-09-30 16:23:04 | `64a01e065bf8736c5745afb617e4315297b3ca6c` | `fc54437a804858732e4bc927add98e202d879a09` |
| [8.7.0](https://github.com/homeassistant-ai/ha-mcp/releases/tag/v8.7.0) | 2026-10-14 14:53:23 | `da72390465b4e14244228478549d7eb362b00637` | `a96752286be1a6511f32a62e90213641c8d18068` |
| **[9.0.0 selected](https://github.com/homeassistant-ai/ha-mcp/releases/tag/v9.0.0)** | **2026-10-28 16:27:13** | `e6f2d2756471e6109f52ce4967ed443698299fca` | `948536dfba941ae058c8b633767918d4b6df2497` |

**Fact:** the latest development release observed was
[v9.1.0.dev3330](https://github.com/homeassistant-ai/ha-mcp/releases/tag/v9.1.0.dev3330),
`4d13f9d63b856524dfe5b91e405c5e937638d3b2`, published
2026-11-02 20:32:32 UTC, `prerelease=true`. No stable 9.1.0 or separately named
release-candidate tag appeared in the tag inventory. **Inference:** 9.1 is in
preparation; "imminent" is not a verified delivery date. It is not the review pin.
The earlier 9.x claim is now established from tags, independently of the app's
update flag.

**Fact / packaging caveat:** `v9.0.0`'s `pyproject.toml` and component manifest say
9.0.0, while its stable app `config.yaml` still says 8.7.0. HAMCP-187 documented
post-tag app publication commits for earlier releases. Do not assume that the
source tag is the published app image revision; bind that independently in a
future admission. [U:pyproject.toml:7](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/pyproject.toml#L7), [U:custom_components/ha_mcp_tools/manifest.json:27](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/manifest.json#L27),
[U:homeassistant-addon/config.yaml:3](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/homeassistant-addon/config.yaml#L3), [E:docs/HAMCP_187_HA_MCP_870_REVIEW.md:74](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/HAMCP_187_HA_MCP_870_REVIEW.md#L74).

## 2. MCP protocol compatibility

**Verdict: compatible (source-level); runtime acceptance unverified.** No source
reason was found to broaden Engineering's protocol allowlist. This verdict does
not make 9.0.0 usable by the current Engineering release.

| Boundary | Pinned upstream behavior | Engineering expectation / assessment |
|---|---|---|
| Version negotiation | Handshake revisions include 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25. A supported requested revision is returned unchanged; only an unsupported request selects the latest handshake revision. Modern-only is 2026-07-28. [U:src/ha_mcp/_vendor/mcp_types/version.py:33](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/_vendor/mcp_types/version.py#L33), [U:src/ha_mcp/_vendor/mcp/server/runner.py:421](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/_vendor/mcp/server/runner.py#L421). | Exactly 2025-03-26 is admitted. The SDK adapter explicitly sends it with empty client capabilities and saves server capabilities before sending initialized. [E:hass_mcp_engineering_beta/ha_mcp_engineering/mcp_sdk_compatibility.py:177](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/mcp_sdk_compatibility.py#L177), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py:134](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L134). Compatible. |
| Transport/session | Standalone Streamable HTTP remains `stateless_http=True`; the modern handler is selected only for a non-handshake protocol header. [U:src/ha_mcp/__main__.py:310](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/__main__.py#L310), [U:src/ha_mcp/_vendor/mcp/server/streamable_http_manager.py:194](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/_vendor/mcp/server/streamable_http_manager.py#L194). | Engineering creates a bounded Streamable HTTP session, initializes, paginates tools, validates the catalog, then calls in that same session; closes with termination enabled. [E:hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py:332](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L332). No new session fallback is needed by source inspection. |
| Capabilities / approvals | Legacy initialize emits the server's capabilities. Policy middleware is opt-in; MRTR requires a modern revision and the same leaf call. [U:src/ha_mcp/_vendor/mcp/server/runner.py:428](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/_vendor/mcp/server/runner.py#L428), [U:src/ha_mcp/server.py:635](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/server.py#L635), [U:src/ha_mcp/policy/mrtr.py:34](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/policy/mrtr.py#L34). | Empty client capabilities do not opt into roots, sampling, elicitation or MRTR. Legacy policy denial/blocking remains an error/timeout consideration, not Engineering approval. Actual production policy options are unknown. [E:hass_mcp_engineering_beta/ha_mcp_engineering/mcp_sdk_compatibility.py:195](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/mcp_sdk_compatibility.py#L195). |
| Tool listing | Public names, annotations, descriptions and conditional schemas must be recaptured. Reads explicitly carry four hints; search has component-dependent schema rewriting. [U:src/ha_mcp/tools/tool_hints.py:17](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tool_hints.py#L17), [U:src/ha_mcp/transforms/component_search.py:59](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/transforms/component_search.py#L59). | Bounded pagination rejects malformed/duplicate/looping catalogs; per-tool exact fingerprints include inputs, outputs, annotations and dispatch-relevant descriptors. Descriptions/hints cannot self-admit. [E:hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py:406](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L406), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py:6330](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L6330). |
| Result/error shape | Dictionary results retain broad object schema inference; `ToolError` still carries JSON with `success:false` and an `error` object, and becomes `isError`. Log-level control was added to the exception constructor. [U:src/ha_mcp/tools/helpers.py:45](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/helpers.py#L45), [U:src/ha_mcp/_vendor/fastmcp/tools/function_parsing.py:414](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/_vendor/fastmcp/tools/function_parsing.py#L414). | Transport envelope validation, error classification, bounded output, projection and sanitation remain mandatory. A broad object schema cannot establish semantic compatibility. [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py:4080](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L4080), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py:4276](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L4276). |

**Exact authorities:** Engineering pins MCP Python SDK 1.30.0,
[E:hass_mcp_engineering_beta/requirements.txt:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/requirements.txt#L1), upstream tag SHA
`8c2fa6eabacde869d75568e33b9d466b58c2f512`. Its ordinary
[client initialize](https://github.com/modelcontextprotocol/python-sdk/blob/8c2fa6eabacde869d75568e33b9d466b58c2f512/src/mcp/client/session.py#L161)
requests 2025-11-25; Engineering's adapter overrides that request. The relevant
[MCP specification](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/9236eb1cbfa02c17ab45c83a7bdbe55c450070be/docs/specification/2025-03-26/basic/lifecycle.md)
is tag 2025-03-26, SHA `9236eb1cbfa02c17ab45c83a7bdbe55c450070be`;
[transport rules](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/9236eb1cbfa02c17ab45c83a7bdbe55c450070be/docs/specification/2025-03-26/basic/transports.md)
cover optional session IDs and protocol headers. Upstream 9.0.0 vendors
fastmcp-slim 4.1.2 and mcp/mcp-types 2.3.0, **not** HAMCP-187's unchanged 2.2.0
tree. The negotiation/version/HTTP manager files compared byte-identical between
8.5.0 and 9.0.0; other SDK files changed, including modern transport,
dispatchers and auth handlers. [U:src/ha_mcp/_vendor/requirements.txt:10](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/_vendor/requirements.txt#L10).

**Inference:** legacy wire compatibility is retained; neither an upstream SDK
bump nor the same handshake algorithm proves assembled-image correctness.
Required future evidence is exact-image initialize + full paginated list +
success/error calls with the pinned Engineering SDK, both architectures,
reconnect/timeout/notification behavior and same-session refusal checks.

## 3. Exhaustive provider/tool contract comparison

### Inventory and exact-match outcome

**Fact:** the requested 29 reads reconcile as **24 automatic stock reads + one
blueprint list/get projection + three platform projections + one optional YAML
fragment projection**. The three platform tools and `ha_get_hacs_info` are
included in 29, not additional reads. The context command's 28 is its stock
projection count, excluding optional YAML. The YAML siblings are bound as
**prohibited**, not admitted reads. [E:hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy_8_5_0.json:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy_8_5_0.json#L1),
[E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_blueprint.py:9](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_blueprint.py#L9),
[E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py:16](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L16),
[E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py:1781](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py#L1781).

**Fact:** 8.5.0's compiled entry is `ha-mcp-v8.5.0-e1538bcd`, source
`311d6dc273fb4e9a5b8cde0de15f69472a64fe44`, policy
`upstream_tool_policy_8_5_0.json`. Provider dispositions are
`read_gateway=admitted`, `dashboard=admitted`, `backup=held`, `lifecycle=held`.
The registry's stock catalog has 77 tools; that is not the owner-reported
80-tool beta/filesystem runtime catalog. [E:hass_mcp_engineering_beta/ha_mcp_engineering/upstream_release_registry.json:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/upstream_release_registry.json#L1),
[E:docs/evidence/upstream-read-compatibility/ha-mcp-8.5.0.json:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/evidence/upstream-read-compatibility/ha-mcp-8.5.0.json#L1),
[E:docs/evidence/hamcp162/captures/catalog-8.5.0-A.json:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/evidence/hamcp162/captures/catalog-8.5.0-A.json#L1).

**Every 9.0.0 row is refused by current Engineering admission.** There is no
compiled 9.0.0 release or per-version binding. Existing matching compares more
than JSON structure; changed descriptions/annotations/meta also matter. A changed
input is refused, not silently interpreted as its old schema. **No current
misread path is claimed.** "Misread" below means the consequence of a future
incorrectly reused output contract or overly broad admission. Current signed
readmission cannot manufacture missing version-scoped adapters or override held
provider dispositions. [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py:6419](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L6419),
[E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py:1756](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py#L1756),
[E:hass_mcp_engineering_beta/ha_mcp_engineering/ha_mcp_readmission/registry.py:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/ha_mcp_readmission/registry.py#L1).

Classification counts for the **38 actual dependency tool names** below (29
reads + nine additional dependencies/siblings): **11 unchanged, 12 compatible
change, 11 needs Engineering change, four blocking**. These are functional
classifications after separating the universal need for reviewed 9.0.0 bindings.
"Blocking" here concerns restoring the **held operational provider**, not a
claim that unrelated reads cannot be admitted. Two historical alias names and
one new-admission candidate are analyzed separately and excluded from the counts.

### Shared schema, error and redaction contracts

Each row below includes these contracts by reference; the raw source table gives
its complete old/new input declaration and return annotation.

- **O — object output:** `dict[str, Any]` on both sides. Source inference is an
  unconstrained object (`additionalProperties:true`), not a typed payload
  promise. Exact 9.0.0 advertised outputSchema remains uncaptured. **D — dashboard:**
  `dict[str, Any] | ToolResult` permits normal config responses and content-rich
  responses; do not infer its wire schema from a dictionary-only tool.
  [U:src/ha_mcp/_vendor/fastmcp/tools/function_parsing.py:414](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/_vendor/fastmcp/tools/function_parsing.py#L414),
  [U:src/ha_mcp/tools/tools_config_dashboards.py:1647](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_dashboards.py#L1647).
- **E — common error contract:** structured JSON error via `ToolError`/`isError`,
  with authentication, connection, timeout, validation and service/command
  failures classified by shared helpers. Raw exceptions are not guaranteed to
  preserve every historical message. Specific omissions/not-found/degraded
  cases are noted per row; runtime error capture is unknown.
  [U:src/ha_mcp/tools/helpers.py:45](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/helpers.py#L45), [U:src/ha_mcp/errors.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/errors.py#L1).
- **R — common redaction:** upstream known-secret output scrubbing is conditional
  on `redact_secrets`; its default is false in app options. Visibility middleware
  can additionally refuse hidden-entity data. Engineering independently sanitizes
  and bounds results, marks redaction as partial, and preserves attribution.
  No row's `readOnlyHint` proves secret safety. [U:src/ha_mcp/server.py:293](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/server.py#L293),
  [U:homeassistant-addon/config.yaml:50](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/homeassistant-addon/config.yaml#L50),
  [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py:4276](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L4276).
- **P — platform projection:** Engineered allowlisted fields, string/list caps,
  email masking where specified, and explicit unavailable/incomplete results;
  not raw app options/diagnostics. [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py:341](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L341).
- **Y — YAML projection:** path/key admission, structural masking and final
  sanitizer remain Engineering-owned; the upstream component is not a secret
  boundary by itself. [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py:81](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py#L81).

### The 29 reads

All rows are source facts except the compatibility assessment and future effects,
which are inferences. "Same inputs" means same types/defaults/constraints, not
identical description bytes. "Unchanged" is limited to the inspected input/function/output contract; shared client, middleware, component and Core effects remain subject to the stated pairing unknowns. **Current match: refuse 9.0.0 in every row.**

| Tool / classification | Input delta, output and semantics vs 8.5.0 | Error / redaction and Engineering implication | Exact 9.0.0 source |
|---|---|---|---|
| ha_config_get_automation — unchanged | Same identifier input; O, retrieval body unchanged. | E/R; shared admin-token checks still apply; rebind descriptor. | [U:src/ha_mcp/tools/tools_config_automations.py:535](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_automations.py#L535) |
| ha_config_get_script — unchanged | Same identifier input; O, retrieval body unchanged. | E/R; shared admin checks, rebind. | [U:src/ha_mcp/tools/tools_config_scripts.py:119](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_scripts.py#L119) |
| ha_config_get_scene — needs Engineering change | Required scene_id becomes optional; query, search_in_config, limit 1–100/default 20 and offset added. O supports list/search as well as get. | E/R; preserve explicit get projection, do not accidentally expose new modes. Existing 8.6 adapter is version-specific. | [U:src/ha_mcp/tools/tools_config_scenes.py:306](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_scenes.py#L306), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_reads_8_6.py:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_reads_8_6.py#L1) |
| ha_config_get_calendar_events — unchanged | Same calendar/start/end inputs; O, body unchanged. | E/R, token/admin behavior from shared client; descriptor rebind. | [U:src/ha_mcp/tools/tools_calendar.py:167](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_calendar.py#L167) |
| ha_config_get_category — unchanged | Same inputs; O, body unchanged. | E/R; no new result redactor in function. | [U:src/ha_mcp/tools/tools_categories.py:45](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_categories.py#L45) |
| ha_config_get_label — unchanged | Same inputs; O, body unchanged. | E/R; rebind. | [U:src/ha_mcp/tools/tools_labels.py:592](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_labels.py#L592) |
| ha_config_list_dashboard_resources — compatible change | Same inputs; O adds resource_mode and YAML-mode note; existing resources/pagination retained. | E/R; suggestions simplified; content previews can contain user JS/text, so retain sanitizer. | [U:src/ha_mcp/tools/tools_resources.py:368](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_resources.py#L368) |
| ha_config_list_groups — needs Engineering change | Same limit/offset. O now merges Core attributes/state fields, removes manufactured all/order defaults, adds auto default false. | E/R; old clients could mistake absent all for false; public-output acceptance must cover change. | [U:src/ha_mcp/tools/tools_groups.py:110](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_groups.py#L110) |
| ha_config_list_helpers — compatible change | helper_type broadens to storage type/all/string; describe, menu_choice, helper_id added. O keeps list shapers, adds metadata/degraded warnings. | E/R; flow enumeration failure can raise SERVICE_CALL_FAILED; secret_scrub_degraded warns that resolved secrets were not scrubbed. Withhold new modes until reviewed. | [U:src/ha_mcp/tools/tools_config_helpers.py:91](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_helpers.py#L91), [U:src/ha_mcp/tools/config_helpers/listing.py:14](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/config_helpers/listing.py#L14) |
| ha_eval_template — needs Engineering change | Optional template plus condition, variables, strict; timeout now constrained 1–60. O no longer includes historical request_id/evaluation_time. | E/R; use reviewed template-only constraints and rebind 8.6-style projection; condition is not automatically admitted. | [U:src/ha_mcp/tools/tools_utility.py:330](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_utility.py#L330), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_reads_8_6.py:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_reads_8_6.py#L1) |
| ha_get_automation_traces — compatible change | Same input types; O trace records retained; legacy info notifications removed. | E/R; trace payload may contain sensitive action data. No notification dependency found in Engineering client. | [U:src/ha_mcp/tools/tools_traces.py:44](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_traces.py#L44) |
| ha_get_device — unchanged | Same inputs; O function body unchanged, enrichment through shared helpers. | E/R; Core child-device semantics require separate version-bound proof, not inferred here. | [U:src/ha_mcp/tools/tools_registry.py:774](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_registry.py#L774), [E:hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/routes.py:600](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/routes.py#L600) |
| ha_get_entity — compatible change | Same inputs; O adds uses_device_name in shared formatter. | E/R; additive output, not a new action. | [U:src/ha_mcp/tools/tools_entities.py:91](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_entities.py#L91), [U:src/ha_mcp/tools/tools_entities.py:1822](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_entities.py#L1822) |
| ha_get_entity_exposure — unchanged | Same inputs; O body unchanged. | E/R; list/get only, exposure writes remain separate. | [U:src/ha_mcp/tools/tools_voice_assistant.py:206](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_voice_assistant.py#L206) |
| ha_get_hacs_info — needs Engineering change | Same input structure; O retains top-level success response; uses per-request credentials. | E/R; special success-envelope model has only reviewed version keys, no 9.0.0. Core 2026.11/HACS issue remains Part B. | [U:src/ha_mcp/tools/tools_hacs.py:130](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_hacs.py#L130), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py:163](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L163) |
| ha_get_history — needs Engineering change | Adds summary=false, include_schema=false, core_options; minute look-backs; O history/statistics payload changed, detailed below. | E/R; warnings outside data, incomplete summary must not be read as complete history; preserve bounded reviewed inputs. | [U:src/ha_mcp/tools/tools_history.py:209](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_history.py#L209), [U:src/ha_mcp/tools/history_response.py:32](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/history_response.py#L32) |
| ha_get_overview — compatible change | Same input structure; O overview plus changed tool-discovery advisory text. | E/R; settings/update/link fields are untrusted content, not authority or executed instructions. | [U:src/ha_mcp/tools/tools_search.py:430](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_search.py#L430) |
| ha_get_skill_guide — needs Engineering change | skill selector removed; file defaults to SKILL.md. O content and BPS acknowledgement behavior. | E/R; missing/path-denied guide raises; Engineering still rewrites only 8.6.0, so a merely rebound 9.0 dashboard write can be refused by BPS. | [U:src/ha_mcp/server.py:956](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/server.py#L956), [E:hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py:359](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L359) |
| ha_get_state — unchanged | Same inputs; O body unchanged. | E/R; typed power/fan state validation remains necessary. | [U:src/ha_mcp/tools/tools_search.py:703](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_search.py#L703) |
| ha_get_todo — unchanged | Same inputs; O body unchanged. | E/R; task text remains untrusted/sanitized. | [U:src/ha_mcp/tools/tools_todo.py:45](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_todo.py#L45) |
| ha_get_zone — compatible change | Same nullable zone_id type; accepts entity_id too. O adds entity_id, editable/source, registry icon and YAML-zone enrichment. | E/R; component failures may fall back internally to legacy reads with warnings; do not interpret incomplete listing as absence. This is upstream behavior, not permission for an Engineering fallback. | [U:src/ha_mcp/tools/tools_zones.py:170](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_zones.py#L170), [U:src/ha_mcp/tools/tools_zones.py:307](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_zones.py#L307) |
| ha_list_floors_areas — unchanged | Same inputs; O body unchanged. | E/R; Core-linked semantics remain separately versioned. | [U:src/ha_mcp/tools/tools_areas.py:142](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_areas.py#L142) |
| ha_list_services — unchanged | Same inputs/limits; O body unchanged (Annotated descriptions added). | E/R; typed provider still bounds domain/service discovery. | [U:src/ha_mcp/tools/tools_services.py:65](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_services.py#L65) |
| ha_search — needs Engineering change | Reference signature largely text changes; component transform removes search_types/include_config/config_time_budget. O compact results and config-only search semantics differ. | E/R; partial/warnings mandatory, preserve conditional descriptor pair and bounded projection. No inference of absence from partial search. | [U:src/ha_mcp/tools/tools_search.py:60](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_search.py#L60), [U:src/ha_mcp/transforms/component_search.py:22](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/transforms/component_search.py#L22), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_search_8_6.py:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_search_8_6.py#L1) |
| ha_manage_blueprints → ha_get_blueprint — compatible change | Same action/domain/path inputs; O list/get semantics; mixed write actions remain upstream-only. | E/R; exact adapter permits only constructed list/get arguments and validates identity/count. New binding needed; do not admit import/delete. | [U:src/ha_mcp/tools/tools_blueprints.py:164](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_blueprints.py#L164), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_blueprint.py:9](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_blueprint.py#L9) |
| ha_get_system_health — compatible change | Input type intent retained; constraints on nullable diagnostic limits represented differently. O section payloads; component routing. | E/R/P; use existing allowlisted sections, exclude diagnostics/config_check and other unsafe branches. Exact 9.0 source binding needed. | [U:src/ha_mcp/tools/tools_system.py:481](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_system.py#L481), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py:43](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L43) |
| ha_get_app — compatible change | Same input structure, O installed app records; operator-listed option-key/PEM redaction enhanced. | E/R/P; projected installed inventory excludes raw options. Source-level redaction gain does not authorize them. | [U:src/ha_mcp/tools/tools_addons.py:3702](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_addons.py#L3702), [U:src/ha_mcp/redaction.py:216](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/redaction.py#L216) |
| ha_get_integration — compatible change | Same input types; O component-path options became nested after 8.5.0. | E/R/P; Engineering sends include_options=false, so flattening change is outside its projection; keep that refusal boundary. | [U:src/ha_mcp/tools/tools_integrations.py:410](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_integrations.py#L410), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py:292](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L292) |
| ha_config_get_yaml — needs Engineering change | Same file/yaml_path/include_parsed structure; O named/glob resolution and failure semantics refined; hidden-definition guard added. | E/R/Y; optional binding currently only 8.4.3/8.5.0; new component error/visibility/parse cases need exact tests. Siblings stay prohibited. | [U:src/ha_mcp/tools/tools_yaml_read.py:374](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_yaml_read.py#L374), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py:1756](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py#L1756) |

### Nine additional dependencies and held/prohibited surfaces

| Tool / classification | Input/output/semantics | Error / redaction / exact match consequence | Evidence |
|---|---|---|---|
| ha_call_service — compatible change | Same signature structure and tool body; O. New component/self-management and backup access guards surround dispatch. | E/R; typed light/switch/fan calls need exact service/state/target contracts; generic ws_command/service forwarding remains unavailable. Both power/fan use this plus state/services already counted above. | [U:src/ha_mcp/tools/tools_service.py:1817](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_service.py#L1817), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_fan.py:24](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_fan.py#L24), [E:hass_mcp_engineering_beta/ha_mcp_engineering/power/contracts.py:9](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/power/contracts.py#L9) |
| ha_config_get_dashboard — needs Engineering change | mode removed, describe=false added; D. Query can span dashboards; bounded normal reads still possible. | E/R; describe may execute custom-card JS in Core; keep describe/screenshot/search withheld unless separately reviewed. Exact read contract/attestation and payload hashes need revalidation. | [U:src/ha_mcp/tools/tools_config_dashboards.py:1647](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_dashboards.py#L1647), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_contracts.py:450](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_contracts.py#L450) |
| ha_config_set_dashboard — needs Engineering change | Same current input structure except text; D. Config hash/optimistic lock retained, malformed-list refusal and card advice paths changed. | E/R; BPS guide issue, component-side validation/advisory work and atomicity/timeout evidence must be handled; no dashboard write admitted here. | [U:src/ha_mcp/tools/tools_config_dashboards.py:2470](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_dashboards.py#L2470), [E:hass_mcp_engineering_beta/ha_mcp_engineering/f3_dashboard/atomicity.py:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/f3_dashboard/atomicity.py#L1) |
| ha_list_files — compatible change | Same input types, O directory listing; component controls forbidden paths. | E/R; **prohibited sibling** despite reviewed descriptor. No generic filesystem read admission. | [U:src/ha_mcp/tools/tools_filesystem.py:547](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_filesystem.py#L547), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py:1781](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py#L1781) |
| ha_read_file — needs Engineering change | Same input types, O file text; enforce-mode hidden-definition check now reads YAML before sliced reads. | E/R; **prohibited sibling**; no universal content/PEM masking guarantee. Remains outside YAML projection. | [U:src/ha_mcp/tools/tools_filesystem.py:637](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_filesystem.py#L637), [U:src/ha_mcp/tools/config_file_visibility.py:21](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/config_file_visibility.py#L21) |
| ha_manage_backup — blocking for held provider | O mixed snapshot/edits actions. restore_database changes false→nullable/None; adds selected restore/create parts and safety_backup, plus backup-access policy gate. | E/R; return/success is not recovery proof. Existing 8.5 backup disposition held; cannot simply reuse old arguments/defaults or admit restore/delete. | [U:src/ha_mcp/tools/backup.py:137](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/backup.py#L137), [U:src/ha_mcp/tools/backup_snapshot_restore.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/backup_snapshot_restore.py#L1), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_backup.py:272](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_backup.py#L272) |
| ha_manage_app — blocking for held provider | O mixed installed/store actions, same input types; not a read. | E/R; app option redaction applies, but old lifecycle binding expects ha_manage_addon. Action-specific verification/mapping required; keep held. | [U:src/ha_mcp/tools/tools_addons.py:3769](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_addons.py#L3769), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py:52](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py#L52) |
| ha_reload_core — blocking for held provider | target/entry_id same types (descriptions now explicit), O. Integration reload can affect server itself; component management guard is relevant. | E/R; lifecycle is held; exact target, dispatch and reload verification remain unresolved for 9.0.0. | [U:src/ha_mcp/tools/tools_system.py:216](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_system.py#L216), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py:799](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py#L799) |
| ha_restart — blocking for held provider | Adds safe_mode=false; O. New embedded path defers restart, checks recorder/backup status, can acknowledge scheduling before actual dispatch. | E/R; do not treat acknowledgement as restart success. Held provider requires refusal/no-double-dispatch/reconnect verification; safe mode would disable custom integrations. | [U:src/ha_mcp/tools/tools_system.py:171](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_system.py#L171), [U:src/ha_mcp/tools/system_restart.py:25](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/system_restart.py#L25), [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py:52](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py#L52) |

**Historical lifecycle names:** Engineering constants still name `ha_get_addon`
and `ha_manage_addon`; neither is a registered function name in 8.5.0 or 9.0.0
(the equivalents are `ha_get_app` and `ha_manage_app`). Upstream's renamed-tool
compatibility handling must not be confused with an advertised descriptor matching
an old Engineering provider. **Inference:** current held dispositions correctly
prevent using those old bindings; restoring lifecycle requires a separate exact
contract review. [U:src/ha_mcp/renamed_tools.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/renamed_tools.py#L1),
[E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py:52](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py#L52).

### History: summary, minutes and legacy clients

**Facts:** `min` means minutes; `m` remains 30-day months. Both start and end
relative times use a common reference. `summary=false` preserves row mode;
`summary=true` replaces rows with changes/first_change/last_change/distinct_states
and a state-keyed map of periods/duration_s. Repeated identical states extend a
period; a window-start snapshot is not a change. Without that snapshot, a first
state later in the window counts as one. The limit selects longest-duration
states, not the first N rows; warnings disclose omissions. Summary is ignored
for statistics. [U:src/ha_mcp/tools/tools_history.py:65](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_history.py#L65),
[U:src/ha_mcp/tools/tools_history.py:305](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_history.py#L305), [U:src/ha_mcp/tools/history_response.py:77](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/history_response.py#L77).

**Facts:** 8.7's compact-key expansion/localized timestamps/retained unknown keys,
optional attributes and `last_changed` fallback remain. Statistics have native
Core rows/metadata/reset handling and calendar aggregation work. `include_schema`
and `core_options` remain dynamic Core-contract surfaces. [U:src/ha_mcp/tools/history_response.py:32](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/history_response.py#L32),
[U:src/ha_mcp/tools/tools_history.py:340](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_history.py#L340), [U:src/ha_mcp/tools/statistics_calendar.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/statistics_calendar.py#L1).
**Inference:** a future admission must consciously preserve the public history
contract or publish its change; generic sanitation will not translate this shape
back to 8.5. Keep summary/core_options/include_schema unavailable until their
schema, output completeness and resource limits are reviewed. This review does
not add those fields to Engineering.

### YAML/component dependency

**Facts:** registration is controlled by filesystem/beta settings, not successful
component detection; execution requires component `ha_mcp_tools` services
`read_file`/`list_files`, with per-call availability/version checks. The component
owns ruamel parsing; merely updating the app cannot supply these services.
[U:src/ha_mcp/tools/tools_filesystem.py:98](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_filesystem.py#L98), [U:src/ha_mcp/tools/tools_yaml_read.py:7](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_yaml_read.py#L7).
9.0 adds whole-file hidden-definition checks before sliced YAML reads, and
refines literal/glob ambiguity, parse failures and warnings. A named unreadable
file can fail the whole read rather than masquerading as a missing key; expanded
files may produce warnings. [U:src/ha_mcp/tools/config_file_visibility.py:21](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/config_file_visibility.py#L21),
[U:src/ha_mcp/tools/tools_yaml_read.py:121](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_yaml_read.py#L121).

**Inference:** keep the Engineering YAML allowlist, no package/glob expansion,
secret-key refusal, masking and prohibited sibling bindings. Verify named missing
file, named parse error, glob partial errors, visibility refusal, component
missing/old/unavailable, forbidden path and secret-bearing output. The current
8.5.0+2.2.1 production pairing remains owner-reported; 9.0.0+2.2.1 behavior is
**unknown**, not assumed from the old four-pair capture.
[E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py:81](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py#L81),
[E:docs/HAMCP_187_HA_MCP_870_REVIEW.md:1279](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/HAMCP_187_HA_MCP_870_REVIEW.md#L1279).

## 4. New admissible candidates (recommendations only)

**Fact:** `ha_get_logs` already existed at 8.5.0; the opportunity is a newly
reviewable implementation/subset, not a newly named tool. 9.0 uses native Core
system-log/error-log/logbook paths and Supervisor-backed log routes; fault_log
still requires File & YAML Tools. Inputs add device_id/context_id and allow an
opaque string offset for journald pagination. `structured=true` error logs do
not use the ordinary raw-line limit. [U:src/ha_mcp/tools/tools_logs.py:242](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_logs.py#L242),
[U:src/ha_mcp/client/rest_client.py:930](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/client/rest_client.py#L930), [U:src/ha_mcp/tools/log_sources_logbook.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/log_sources_logbook.py#L1),
[U:src/ha_mcp/tools/log_sources_supervisor.py:40](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/log_sources_supervisor.py#L40),
[S:src/ha_mcp/tools/tools_logs.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_logs.py#L1).

| Candidate recommendation | Engineering value | Risk and required admission evidence |
|---|---|---|
| Investigate a bounded ha_get_logs native Core subset | Correlate system errors/logbook events and preserve exact journal pagination. | Secrets/addresses/household activity in logs, large traceback records, cursor validity, truncation, HA-vs-Supervisor token/role distinctions. Require exact source/image descriptor, typed source allowlist, byte/time/window limits, redaction and positive/refusal tests. Never admit arbitrary slug, unrestricted structured mode or fault-log filesystem reads through a blanket read classification. [U:src/ha_mcp/tools/tools_logs.py:242](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_logs.py#L242), [U:src/ha_mcp/tools/log_sources.py:57](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/log_sources.py#L57). |
| Consider history summary as an explicit extension | Smaller state-duration answers, clearer change counts. | Missing start state, purged history, limit omission, calendar/timezone semantics; publish versioned shape and refusal cases rather than treating generic object output as sufficient. [U:src/ha_mcp/tools/history_response.py:77](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/history_response.py#L77). |
| Consider helper describe only as a bounded metadata projection | Read Core-supported helper fields without enabling helper writes. | Component-dependent schemas and degraded flow enumeration; pin contracts and prove absence of mutations. [U:src/ha_mcp/tools/tools_config_helpers.py:91](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_config_helpers.py#L91). |
| Do not recommend generic dashboard describe admission now | Could expose custom-card configuration guidance. | Executes custom-card JS and optional dependency acquisition in Core; unresolved HAMCP-187 cache/owner concerns. Existing normal dashboard read has less scope. [U:src/ha_mcp/tools/dashboard_card_describe.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/dashboard_card_describe.py#L1), [U:custom_components/ha_mcp_tools/custom_cards.py:582](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/custom_cards.py#L582). |

These recommendations do not create policy entries, signatures, tool registration
or a CI candidate slot. Existing Engineering native log providers remain separate
contracts, [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/core_logs.py:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/core_logs.py#L1).

## 5. Component and app changes; carry-forward security findings

**Fact:** the 9.0.0 component now declares its own 9.0.0 version and a
`ha-mcp==9.0.0` dependency. Component update and app update are separate changes;
a component-only update can therefore alter the Core Python environment even
when the app remains 8.5.0. Its exact runtime consequences for the owner's setup
are unknown without a disposable pairing test. [U:custom_components/ha_mcp_tools/manifest.json:27](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/manifest.json#L27).

| Change / fact | Production effect and owner action if adoption is later chosen | Security assessment |
|---|---|---|
| Access switch, disabled entity by default | Applies to an **in-process server entry**, not the standalone app or File & YAML entry. Off closes direct port/webhook/LLM paths together; optional off-after-restart exists. No current owner action. Do not assume it shuts down the deployed app. [U:custom_components/ha_mcp_tools/switch.py:33](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/switch.py#L33), [U:custom_components/ha_mcp_tools/embedded_entry.py:175](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/embedded_entry.py#L175). | Useful owner control; exposing the switch to agents/automations is an independent authority decision. Default-disabled entity avoids accidental all-switch service calls. |
| Admin-token in-process server | New setup accepts a valid long-lived admin token or issues one using a local-only admin login; old generated credentials can continue if valid. Missing/revoked credentials refuse start. No credential action is required for this report or the existing app topology. [U:custom_components/ha_mcp_tools/server_credentials.py:69](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/server_credentials.py#L69). | Token stored in entry data, handed to server in memory instead of process environment. Does not make token persistence risk disappear. Never ask an agent to retrieve/set production tokens as part of this review. [U:custom_components/ha_mcp_tools/embedded_server.py:1325](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/embedded_server.py#L1325). |
| Locked-down new-entry defaults | New in-process setups bind loopback and disable webhook until owner chooses access. Existing entries retain explicitly stored/inherited behavior; no silent conversion to the new topology is recommended. [U:custom_components/ha_mcp_tools/config_flow.py:245](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/config_flow.py#L245). | Reduced initial exposure, not proof existing entries became isolated. Inspect only in a future authorized deployment assessment. |
| Removed component self-update entity | Retired update entity removed; component/server pairing follows manifest/pin machinery. Owner should control HACS/component updates separately and keep the parked 2.2.1 decision until reviewed. [U:custom_components/ha_mcp_tools/embedded_entry.py:273](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/embedded_entry.py#L273), [U:custom_components/ha_mcp_tools/server_pin.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/server_pin.py#L1). | Does not mean all update/developer tools vanished. Do not enable developer tools or assume self-update removal eliminates dependency installation. |
| Generic integration-tool refusal of ha_mcp_tools entries | Generic set/remove/reload-management paths refuse the server's own component entries; HA UI/configuration ownership is explicit. The three projected platform reads do not need write bypasses. [U:src/ha_mcp/tools/config_entry_flow.py:759](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/config_entry_flow.py#L759), [U:src/ha_mcp/tools/tools_integrations.py:2267](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_integrations.py#L2267). | Positive narrowing; component developer tools are not admitted through Engineering. Requires negative tests for aliases and entry IDs in any later action review. |
| OAuth callback allowlist | Server component validates permitted redirect callbacks; this is component OAuth behavior, not proof the independent proxy or hosted Nabu Casa routing changed. Owner must review callback needs only if choosing that topology. [U:custom_components/ha_mcp_tools/oauth_redirect_allowlist.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/oauth_redirect_allowlist.py#L1). | Default allowlist/explicit override is a separate boundary; no reason to relax it for this report. |
| Operator-listed app option redaction | redact_app_option_keys accepts comma/newline names and app_slug:name scopes; group names cover nested options, retained old option shapes are scrubbed too. Empty by default; only useful with redact_secrets enabled. Owner may later enumerate non-password secret option keys without disclosing values. [U:src/ha_mcp/redaction.py:168](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/redaction.py#L168), [U:src/ha_mcp/redaction.py:272](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/redaction.py#L272), [U:src/ha_mcp/config_settings.py:556](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/config_settings.py#L556). | Improvement for schemas that label a secret as plain text. Missing schemas mask all option values; explicit scoped names not found generate warnings. Not universal key-name detection or proof every log/file is redacted. |
| Generated app options | Compared with 8.5 tag: remove backup_hint; add custom_instructions, enable_snapshot_actions=true, backup_read_only=false, redact_app_option_keys=""; retain snapshot-delete=false/min-age 7 and security policies=false. secret_path remains optional schema. [U:homeassistant-addon/config.yaml:41](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/homeassistant-addon/config.yaml#L41), [S:homeassistant-addon/config.yaml:41](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/homeassistant-addon/config.yaml#L41). | Owner must review migrated options in a future disposable/app acceptance; defaults do not prove current values. Backup enablement never overrides Engineering's held provider. No option changes now. |

### HAMCP-187 findings reverified or carried forward

| Prior finding | 9.0.0 result and limit |
|---|---|
| #2695 helper removal may delete a foreign integration | **Fact: fix retained.** Registry platform must equal requested helper type; mismatches raise before deletion. Old 8.5 source still has the historical unchecked lookup. Not reachable through the reviewed automatic-read gateway; preserving 8.5 rollback is not a claim the old direct-client defect is fixed. [U:src/ha_mcp/tools/flow_helper_lookup.py:208](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/flow_helper_lookup.py#L208), [S:src/ha_mcp/tools/tools_integrations.py:413](https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_integrations.py#L413), [E:docs/HAMCP_187_HA_MCP_870_REVIEW.md:170](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/HAMCP_187_HA_MCP_870_REVIEW.md#L170). |
| #2613 app discovery publishes secret-path URL | **Fact: remains, now Core-version-gated.** announce_mcp_discovery reads secret path, checks Core supports discovery, posts app URL; no user opt-out in this function. Do not claim it always announces on older Core. Owner's earlier do-not-Configure/Ignore rule remains a historical decision; actual discovery/storage state unknown. [U:src/ha_mcp/app_discovery.py:50](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/app_discovery.py#L50), [E:docs/HAMCP_187_HA_MCP_870_REVIEW.md:1386](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/HAMCP_187_HA_MCP_870_REVIEW.md#L1386). |
| #2671 custom-card JS / dependency supply chain | **Fact: partly improved, unresolved.** linkedom tarball is SHA-512 checked, but cached JS is read directly without re-verification. QuickJS 0.17.0.1 is optional, checks existing providers/consumers/Core constraints, and installs through Core's requirements mechanism when missing; no wheel hash in that requirement. Custom JS remains executed for advice. [U:custom_components/ha_mcp_tools/custom_cards.py:201](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/custom_cards.py#L201), [U:custom_components/ha_mcp_tools/custom_cards.py:582](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/custom_cards.py#L582), [U:custom_components/ha_mcp_tools/card_runtime.py:76](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/card_runtime.py#L76). No global opt-out was established in these paths; skip_pip/installation failure is not an equivalent reviewed owner control. |
| All descriptors need rebinding / conditional search | **Retained.** Four-hint annotations, removed legacy meta, descriptions and component transforms mean old catalog bytes are insufficient. Exact 9.0 whole-catalog counts/fingerprints are unknown here. [U:src/ha_mcp/tools/tool_hints.py:17](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tool_hints.py#L17), [U:src/ha_mcp/transforms/component_search.py:59](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/transforms/component_search.py#L59), [E:docs/HAMCP_187_HA_MCP_870_REVIEW.md:228](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/HAMCP_187_HA_MCP_870_REVIEW.md#L228). |
| 8.6-only skill-guide argument rewrite | **Still present in current Engineering.** No 9.0 key. Positive BPS acknowledgement and refused malformed guide calls must be proven before claiming dashboard compatibility. [E:hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py:359](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L359), [U:src/ha_mcp/server.py:956](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/server.py#L956). |
| No optional YAML binding existed in the old review | **Superseded by current source.** HAMCP-162 now binds YAML for 8.4.3/8.5.0 and prohibits sibling file tools; 8.6 remains held. [E:hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py:1795](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py#L1795). |
| Four-pair capture / 79-versus-80 count / signatures | Historical capture covers only 8.5/8.7 × component 2.2.0/2.2.2. Owner's 80 count is not a 9.0 observation. Published 9.0 image provenance/signature verification was not performed; do not repeat historical "none" as a fresh artifact finding. [E:docs/HAMCP_187_HA_MCP_870_REVIEW.md:1067](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/HAMCP_187_HA_MCP_870_REVIEW.md#L1067), [E:docs/HAMCP_187_HA_MCP_870_REVIEW.md:1279](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/HAMCP_187_HA_MCP_870_REVIEW.md#L1279). |

## 6. Nabu Casa webhook proxy since 3.0.4

**Fact:** the stable proxy directory at pinned ha-mcp 9.0.0 is still version
3.0.4. Its last change is promotion commit
`10d63fa7e23cd172c5f8c9e258ff3a4cac566b63` (2026-10-09 local / October 10 UTC);
`git diff 10d63fa7..948536df -- homeassistant-addon-webhook-proxy` is empty.
Thus **no stable proxy source delta since 3.0.4 at this review pin**.
[U:homeassistant-addon-webhook-proxy/config.yaml:3](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/homeassistant-addon-webhook-proxy/config.yaml#L3),
[U:homeassistant-addon-webhook-proxy/CHANGELOG.md:5](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/homeassistant-addon-webhook-proxy/CHANGELOG.md#L5).

**Fact:** 3.0.4 already contains the `/readonly` route and cloudhook buffering:
60-second timeout returns 504, and cloudhook OAuth discovery is refused when the
challenge cannot be relayed. These are baseline 3.0.4 behaviors, not changes
introduced by 9.0.0. Normal webhook requests and Cloud relay MockRequests follow
different branches. [U:homeassistant-addon-webhook-proxy/mcp_proxy/cloudhook.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/homeassistant-addon-webhook-proxy/mcp_proxy/cloudhook.py#L1),
[U:homeassistant-addon-webhook-proxy/mcp_proxy/readonly_webhook.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/homeassistant-addon-webhook-proxy/mcp_proxy/readonly_webhook.py#L1).

**Inference:** no proxy update is indicated by this stable source comparison.
Engineering remains the public connector behind the owner's proxy/Nabu Casa
path; changing the internal ha-mcp app does not require a second public connector
or selecting the new in-process server. **Unknown:** installed proxy digest,
actual configured upstream, hosted routing, headers, tenant binding and current
end-to-end reachability. No live check was made. Component `mcp_webhook.py` is a
different handler; its changes do not prove a change to the independently packaged
proxy. [U:custom_components/ha_mcp_tools/mcp_webhook.py:1](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/mcp_webhook.py#L1),
[E:docs/architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md:121](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/docs/architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md#L121).

## 7. Part B status and decision boundary

**Waiting.** On November 2 the GitHub API endpoint
`repos/home-assistant/core/releases/tags/2026.11.0` returned HTTP 404; `git ls-remote --tags` for the exact tag and peeled tag returned no refs. No published stable Core 2026.11.0 identity
was established. Part B is therefore not performed: no Core 2026.11 compatibility
verdict and no completed admission/signing/deployment plan.

**Carried-forward source fact only:** 9.0 still calls `hacs/info` and
`hacs/repositories/list`; this is not proof of behavior under unpublished Core.
[U:src/ha_mcp/tools/tools_hacs.py:56](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_hacs.py#L56), [U:src/ha_mcp/tools/tools_hacs.py:402](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/tools/tools_hacs.py#L402).
[Issue #2713](https://github.com/homeassistant-ai/ha-mcp/issues/2713) is an input to
Part B, not authority to declare failure or compatibility now. `ha_manage_hacs`
is not admitted merely because the read is under review.

When the gate opens, Part B must resolve Core 2026.11.0's tag SHA and inspect its
source, recheck the stable upstream pin, then address the compiled release entry,
explicit provider dispositions, policy JSON, version-bound projections, Core
binding, CI candidate slot, owner steps, positive/refusal acceptance and rollback.
**Owner constraint carried forward:** seq 16 carries the 31 HAMCP-280 profiles;
new Core profiles require a later sequence. Engineering admission release must be
deployed **before** any ha-mcp app update. These are task requirements, not an
implemented or signed plan. This report reserves no sequence or candidate slot.

## 8. Part A recommendation and review findings

**Recommendation: wait.** The protocol is not the identified blocker; the
unreviewed release/descriptor/output/component contracts are. Useful 9.0 changes
include summary/minute history queries, better option redaction and native log
paths, but they do not justify bypassing exact admission or Josh's parked
component decision. The minimum evidence that could change the recommendation is
architect acceptance of this Part A, Core-pinned Part B, owner resolution of the
component/discovery risks, and passing exact-image/disposable pairing evidence
for a separately authorized admission implementation. See sections 2–7.

| Severity / review finding | Evidence, impact and cause | Proposed correction and proving test (not implemented) |
|---|---|---|
| High — adopting before admission would lose delegated capabilities | No 9.0 compiled entry/bindings; fingerprint matching deliberately refuses. Section 3, [E:hass_mcp_engineering_beta/ha_mcp_engineering/upstream_release_registry.json:1](https://github.com/jeter-1/hass-mcp-admin/blob/f8242b6e82064a5efb5cd0076ff60b4626d5b589/hass_mcp_engineering_beta/ha_mcp_engineering/upstream_release_registry.json#L1). | Separate reviewed admission release first; test authorized reads/power/fan/dashboard success on exact image and refusal of wrong version/schema/provider. |
| High — component supply-chain concern remains unresolved | Cached linkedom unchecked; optional QuickJS installation, JS advice in Core. [U:custom_components/ha_mcp_tools/custom_cards.py:582](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/custom_components/ha_mcp_tools/custom_cards.py#L582). | Josh decides acceptable scope/required upstream change; disposable cache-tamper refusal and dependency/opt-out tests before approving that component. No production exploit test. |
| Medium — public semantic deltas and BPS failure | Groups/history shapes, search transforms, YAML failures and 8.6-only guide rewrite. Section 3. | Version-specific projection/output decisions; test old accepted calls, rejected extras, missing/degraded data, acknowledgement success and malformed-guide refusal. |
| Medium — discovery disclosure persists | App announces secret-path URL on supported Core, [U:src/ha_mcp/app_discovery.py:50](https://github.com/homeassistant-ai/ha-mcp/blob/948536dfba941ae058c8b633767918d4b6df2497/src/ha_mcp/app_discovery.py#L50). | Resolve owner opt-out/exposure decision; disposable tests of announcement, opt-out if supplied, storage and backup consequences. No secret-path access or rotation in this task. |
| Medium — operational provider incompatibility remains held | Backup default/parts changes, app rename, embedded deferred restart; section 3. | Keep backup/lifecycle held unless independently scoped; test exact targets, refusal, partial/lost reply, one-dispatch ownership, verification and recovery. |

These findings describe adoption risks, **not defects introduced by this docs-only
PR**. No Critical finding was established. The report is a source review with
self-review; architect independent review is pending. No adoption decision is
made for Josh.

## 9. Validation, delivery and recovery

Executed source checks: **11/11 passed** (minute/month parsing, summary boundary/limit behavior, three protocol-file byte comparisons, 24-read inventory, provider dispositions, absence of a 9.0 entry and classification counts). **190** pinned report source-link targets resolved locally to existing files and valid lines. The stable proxy directory comparison against its 3.0.4 promotion commit was empty. These are source/document checks, not app execution.

Repository validation results and exact delivery head/PR/CI status are recorded in the
handoff and PR body, avoiding a self-referential commit hash in this report.
The raw source table is not a runtime or persisted-format compatibility fixture.
No 9.0 exact-image, Supervisor, ARM64 or live acceptance is claimed.

Only this report and its adjacent raw table are deliverables. Stable-v1,
Engineering runtime, schemas/registration/provider routes, workflow permissions,
release/version/image/deployment metadata and policies are unchanged. No
admission, signing, app change, credential use, merge, Ready action or deployment
was performed. Recovery is to revert the documentation commit; it changes no
running system. Branch/worktree cleanup is reserved for the post-merge sweep.
