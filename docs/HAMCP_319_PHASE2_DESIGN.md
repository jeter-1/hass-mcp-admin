# HAMCP-319 Phase 2 design: ha-mcp 9.1.0 admission side by side with 8.5.0

Status: **design revision for Codex focused closure.** This document changes no
runtime code, policy, registry, workflow, version or release declaration.
Implementation follows on the same pull request only after Codex closes this
design.

## 0. Baseline, authority and labels

| Item | Value |
|---|---|
| Design base | Written against `release/2.5.0` at `344622076d7f393440d08fdf772f6f53a53cd103` (equal to `main` #382). The branch then merged `release/2.5.0` at `7246f2d21693f039504542574569f03b71476489` (HAMCP-331). That commit changes no Engineering file cited here, so every Engineering file:line citation holds at both revisions. |
| Engineering version at base | `2.4.2-beta.7`, materialized by HAMCP-331. `.release/` holds only `README.md`. |
| ha-mcp 9.1.0 app source | [`d2e3f4b0647d815fb32d530fed15a6a99b1d4b33`][u91] (archive sha256 `382c2e35…08dd`; 816 files verified by Codex against the retained archive) |
| ha-mcp 8.5.0 source | [`311d6dc273fb4e9a5b8cde0de15f69472a64fe44`][u85] (archive sha256 `2cfdc34e…edf1`). The 8.5 image source `8182c6e6…` is a separate identity and is not substituted for it. |
| Optional component | 2.2.1 tools-only [`fc54437a804858732e4bc927add98e202d879a09`][c221]. Component 9.1 is **not** in scope without Josh. |
| SDK / protocol | `mcp==1.30.0` (httpx 0.28.1, httpcore 1.0.9, httpx-sse 0.4.3); protocol `2025-03-26`. Any other negotiated revision fails closed. |
| 9.1 runtime evidence | [Phase 1 evidence](evidence/hamcp319/phase1/README.md): app amd64 `sha256:dad67faa…`, manifest `8952aef8…`, config `f34f81e2…`, Core 2026.11 `74cf5a49…`. Standalone `bef7cfc9…` and arm64 identities are retained Step 1 OCI metadata, **not** executable evidence. Standalone, app and architecture claims stay separate. |
| Inputs answered | Codex design review F1–F8 with positions on A1–A7 (`~/handoffs/hass-mcp-admin-agent-codex-hamcp-319-phase2-design-review.md`); D3 addendum rules 1–9 (`~/handoffs/hass-mcp-admin-agent-hamcp-319-phase2.md`); the exact-source review (`.artifacts/hamcp319-91-source-review.md`, untracked) |

Every decision below is tagged with its authority:

- **[IC]**: implementation choice by Claude Code, the design author. It is open to
  Codex closure and is not a product decision.
- **[JD]**: a Josh decision recorded in the HAMCP-319 register row. Exactly one is
  used: on 2026-11-06 (about 07:00 CST) Josh decided that `ha_get_skill_guide` on
  9.1 is file-only, like the 8.6 adapter (option a). The listing and file-list
  tiers are reported unavailable, and there is no static inventory.
- **[JR]**: a Josh requirement for the 2.5.0 build as relayed in the task brief:
  - 8.5.0 and 9.1.0 are admitted side by side;
  - Engineering is deployed first;
  - rollback to 8.5.0 keeps working;
  - the release notes cover `/catalog/full` and rollback.

  This document relays these requirements and does not add to them.
- **[AR]**: architect ruling R1–R5. **Only the summary is located**: "all redirects
  refused on the 9.1 endpoint; beta.7 if queue holds". The full text was not
  found in the register, handoffs, or PR #368/#375 comments.
- **[AC n]**: architect correction A1–A7, known only as Codex restated and agreed
  them.
- **[pending architect R-text]**: the point rests on R-text that was not located.
  The design states the fail-closed choice it would implement and does not
  invent a ruling.
- Evidence classes follow Codex: **C** captured, **S** source-derived, **U**
  unknown or unexecuted.

## 1. Disposition table

### 1.1 Codex findings

| Finding | Revised decision | Authority | Exact citations |
|---|---|---|---|
| **F1 (High)** The configured URL was bound, not the effective endpoint. | **Two parts.** (a) A strict selector classifier runs over the configured URL (section 3). (b) A version-scoped strict transport is installed through `httpx_client_factory` for every SDK session on a `full`-selector endpoint. It applies to POST initialize, notifications, list pages, tools/call, the GET stream and the DELETE cleanup. It does three things: refuses any request whose URL is not the bound URL exactly, refuses every 3xx response before the SDK sees it, and sets `max_redirects=0`. **No redirect is followed on the 9.1 endpoint**, including same-origin canonicalization. **Admission rule.** After initialize, admission requires agreement between `serverInfo.version` and the selector, *and* proof that the session ran on the strict transport. 9.1.0 requires `full`; every other version requires `none`. **8.5 path.** The current transport for `none` stays unchanged. **Fingerprints.** The order fingerprint comes from `pages[*].tools` order, and per-descriptor, annotation, output-schema and duplicate checks are kept. | [AR] R1 summary; strict-transport design [IC]; 8.5 transport unchanged [IC, pending architect R-text on whether R1 covers 8.5] | SDK [`_httpx_utils.py:113-145`][sdk-stream] follows same-origin 307/308 itself and ignores `follow_redirects`; [`:33-60`][sdk-factory] sets no `max_redirects` (httpx default 20). The 3xx message in [`streamable_http.py:79-94`][sdk-unfollowed] includes the Location path. Engineering SDK call sites: [`clients/upstream_read.py:231`][e-ur231], [`:292`][e-ur292], [`clients/mcp.py:331`][e-mcp331], [`clients/dashboard_analysis.py:455`][e-da455]. Existing fence: [`dashboard_analysis.py:257-317`][e-da257]. Selector grammar: [`http_transport.py:122-172`][u-sel]. Default can be full: [`tool_catalog.py:56-60`][u-cat]. |
| **F2 (High)** The parity contract was unresolved. | **Ledger.** A per-tool public input/output/error contract for all 29 rows (section 7). **Skill guide.** File-only per [JD]; the listing and file-list tiers return a bounded "unavailable on this upstream version" refusal. They are never invented and never called parity. **History statistics.** A compiled 9.1 projection reproduces the exact 8.5 filter: `start` plus requested types, with first-row unit and pagination. Row fields 8.5 never returned are dropped, and fields it returned but 9.1 omits are reported unavailable. **Eval template.** `request_id` and `evaluation_time` are reported unavailable and never synthesized. **Arguments.** Public argument refusals and fixed wrappers are preserved. Pass-through and projection are distinguished per row. | [JD] guide; [AC 4] no fabrication and statistics field selection; projection mechanics [IC] | 8.5 guide [`server.py:1679`][u85-guide]; 9.1 guide [`server.py:935`][u91-guide]; 8.6 file-only adapter [`upstream_reads_8_6.py:656`][e-r86-656], [`:1031`][e-r86-1031]; 8.5 statistics filter [`tools_history.py:1077-1115`][u85-stat], defaults [`:1180`][u85-stat-default]; 9.1 statistics [`tools_history.py:989-1040`][u91-stat]; 9.1 eval [`tools_utility.py:80`][u91-eval]; 8.5 eval fields [`tools_utility.py:30-61`][u85-eval] |
| **F3 (High)** Completeness propagation was missing. | **Treatment table.** A compiled 9.1 completeness and availability table is added to `_upstream_completeness`. It is keyed by `(9.1.0, tool)` and covers every affected read. **Disclosure.** Bounded, sanitized upstream visibility warnings set `partial` or `unavailable`. A mapping removed by visibility is reported as withheld, not as empty. Search coverage is qualified as "visible scope". **Failures.** Authentication, registry-unavailable and malformed results never become empty success. **Typed reads.** Typed state checks still refuse when required attributes are absent. | [IC]; [AC 4] | [`upstream_read_gateway.py:7106`][e-g7106] handles only blueprint, platform and search; result completeness [`:4298`][e-g4298]; 9.1 warning hoisting [`response_helpers.py:392`][u91-hoist]; Phase 1 [`visibility.json`](evidence/hamcp319/phase1/receipts/visibility.json), [`more-edges.json`](evidence/hamcp319/phase1/receipts/more-edges.json) |
| **F4 (High)** Dashboard absence was decided wrongly. | **Three facts are modeled separately:** identity existence, readable saved config, and write-outcome certainty (section 6.3). **No config.** "No config found." or `stored_config:false` means *exists, config unavailable*. It is never treated as empty or as absent. **Absence.** A complete, positively decoded registry list supports absence only for non-default targets. When the list collapses to `[]`, absence cannot be decided and is reported as unavailable. **Hashes.** No empty config or hash is ever synthesized. **Write outcomes.** Upstream `write_outcome_unknown` / `write_committed: None` maps to "outcome uncertain". **Arguments.** `mode` and `query` stay prohibited, so no dashboard search is introduced. | [AC 2] as qualified by Codex; model [IC] | [`dashboard_read_errors.py:29-71`][u91-dre]; list collapse [`tools_config_dashboards.py:1796`][u91-dlist], [`:1005`][u91-dfetch]; built-in `lovelace` [`:3066`][u91-lovelace]; save outcome [`:3339-3400`][u91-save]; Engineering config/hash requirement [`upstream_dashboard.py:582`][e-ud582]; prohibited `mode`/`query` [`upstream_contracts.py:71`][e-uc71] |
| **F5 (Medium)** The YAML tag rule was inaccurate. | **Tag set.** The exact 2.2.1 preserved set is used: `!include`, the four `!include_dir_*` tags, `!secret` and `!env_var`, all kept in source form. **Scanner.** A grammar-aware, bounded tag-token scan runs on transient `content` only. Loss is reported through a closed allowlist plus a generic `unknown_tag` marker, and arbitrary tag text is never echoed. **Raw text.** It is stripped from both channels before downstream handling and never reaches a result, log, audit or error record. **Contracts.** The 9.1 decoder and argument contract are versioned, and 8.5 behavior is unchanged. **Optional tool.** It remains binary-owned and outside the stock policy, and all four file siblings stay prohibited (section 6.4). | [AC 5]; scan design [IC] | 2.2.1 [`yaml_rt.py:42`][c-yaml42], [`:192`][c-yaml192]; [`upstream_yaml_reader.py:1293-1296`][e-y1293]; optional exclusion [`upstream_tool_policy.py:1359-1363`][e-p1359]; [`OPTIONAL_NAMES`][e-y1827] |
| **F6 (Medium)** The outcome matrix was incomplete. | **Matrix.** A per-family outcome matrix for power, fan and helper (section 6.2). **Uncertainty.** Cancellation or timeout after possible dispatch reports uncertainty (`dispatch_indeterminate` or `changed_unverified` per family), with **no retry**. **Pre-send refusal.** The proven `call_not_sent` refusal is kept. **Received responses.** `success:true, partial:true` timeout envelopes and non-admin empty `changed_states` responses count as received and go to observation. They are never proof of change. **Correction.** The earlier claim that "Phase 1 proves server continuation" is withdrawn. | [IC]; rules 7–8 | [`fan/adapter.py:108`][e-fa108]; [`f3/executor.py:357`][e-ex357]; [`helper_value/service.py:87`][e-hv87]; [`fan/service.py:225`][e-fs225]; [`upstream_fan.py:94`][e-uf94], [`:159`][e-uf159]; 9.1 [`tools_service.py:855`][u91-partial]; [`rest_client.py:681`][u91-nonadmin] |
| **F7 (Medium)** Evidence was not tied to payloads. | **Captured fixtures** carry: evidence file, sha256, RFC 6901 JSON pointer, declared normalization, and an equality check against that subtree. **Source-derived fixtures** carry the exact file and line, and stay S until an exact-image run executes them. **Counts and labels.** The schema count is corrected to **14/24** wire-identical with 8.6. `edges.json.timeout` is recorded as a class name and elapsed time, not an envelope. `yaml_component_missing` is relabeled as a successful paired read; component-missing evidence points to `component-flow.json`. **Identities.** Standalone, app and architecture identities stay separate. **Mutations** cover global admission and every typed or provider route. | [IC] | Phase 1 [`provider-delta.json`](evidence/hamcp319/phase1/receipts/provider-delta.json) (77→82, 0/77 identical descriptors); [`edges.json`](evidence/hamcp319/phase1/receipts/edges.json); [`component-flow.json`](evidence/hamcp319/phase1/receipts/component-flow.json) |
| **F8 (Medium)** D9 conflicted with the release queue. | No version change in this item PR. The release base `release/2.5.0` carries the next version, **2.4.2-beta.7**, materialized by HAMCP-331 at `7246f2d2` and merged into this branch. This PR inherits that version and, at implementation, adds only beta.7 release-note and acceptance content. Final Evidence runs once, when next in line, on the pinned final head. | [AR] "beta.7 if queue holds"; [AC 1]; queue mechanics per the release orchestration handoff [pending architect R-text for anything beyond the summary] | `scripts/release_transition.py:48-118` (beta to beta.7 validates; 2.5.0-beta.1 refuses), per the 2.5.0 build handoff |

### 1.2 D3 addendum rules 1–9

| Rule | Revised decision | Authority | Citation |
|---|---|---|---|
| 1. Pass-through vs projection | Every one of the 29 rows is classified as one of three kinds (section 7): **pass-through** (bounded, sanitized and attributed), **projection** (a compiled field map), or **fixed wrapper**. A pass-through row still owes F3 completeness. | [IC]; [AC 4] | [`upstream_read_gateway.py:4133`][e-g4133], [`upstream_platform_reads.py:364`][e-pr364] |
| 2. History statistics | Compiled projection `ha-mcp-9.1.0-history-statistics-v1` derived from the 8.5 filter (section 6.1). **No hold.** | [IC]; [AC 4] | [`8.5 tools_history.py:1077`][u85-stat]; [`9.1 :989`][u91-stat] |
| 3. History state rows | A 9.1 parser row is added to `history_slices` (`REVIEWED_VERSIONS` gains `9.1.0` only together with the parser). It handles expanded keys, optional `attributes` (absent means unavailable, not `{}`) and offset-aware localized timestamps accepted by `parse_upstream_us`. `summary=False`, `include_schema=False` and `core_options=None` are pinned. | [IC] | [`history_slices/contracts.py:35`][e-hs35], [`:160`][e-hs160]; [`history_response.py:49`][u91-hist] |
| 4. get_dashboard "No config found" | **Superseded by F4.** The earlier "map to not-found" is withdrawn as unsafe. The result is now *exists, saved config unavailable*, which fails closed for set, patch, atomicity and attestation, and is never treated as `{}`. | [AC 2] + F4; [IC] | [`dashboard_read_errors.py:29`][u91-dre] |
| 5. Dashboard `openWorldHint: true` | 9.1 dashboard get and set descriptors are reviewed under their own captured annotation fingerprints. Nothing is inherited from 8.5 or 8.6. A changed annotation is a refusal, not an inheritance. | [IC] | Phase 1 [`catalogs/full-1.json`](evidence/hamcp319/phase1/catalogs/full-1.json) (C) |
| 6. `ws_command` safety | Exact constructed allowlist on every typed boundary: domain, service, entity_id, data, `wait:false`, `return_response:false`, `verbose:false`. Anything else, including `ws_command`, `result_fields`, `result_attribute_keys` and unknown keys, is refused before dispatch. Generic `ha_call_service` stays unexposed. | [AC 3] | [`fan/contracts.py:78-85`][e-fc78], [`power/contracts.py:49-52`][e-pc49], [`helper_value/contracts.py:167-170`][e-hc167]; 9.1 forwarder [`tools_service.py:679-802`][u91-ws] |
| 7. Non-admin empty `changed_states` | `result: []` or `{changed_states: [], service_response}` is a *received response*. It goes to observation and is never failure, never `call_not_sent`, never success without readback. | [IC] | [`rest_client.py:681`][u91-nonadmin] (S) |
| 8. Cancellation | After durable intent, cancellation or timeout means uncertain with no retry. Whether HA keeps executing after a client cancel is **UNKNOWN** and is not claimed either way. | [IC] | [`f3/executor.py:357`][e-ex357]; source review §5 |
| 9. Error-contract adapters | A new adapter `ha_mcp_error_contract_9_1_0_v1` is derived from exact 9.1 call paths and labeled `source_derived`. It is never attached by analogy to 8.5's aggregate (`4ed5b1b4…`) or probe fingerprints, for example the `missing_automation` probe `8053f516…`. It is proven only in the exact-image lane; if that lane cannot execute a probe, the limitation is reported. | [IC] | [`ha_mcp_readmission/ha_mcp.py:137-146`][e-err] |

## 2. Rulings and decisions as known

| ID | Known content | Where this design depends on it |
|---|---|---|
| R1 | "All redirects refused on the 9.1 endpoint" (summary only). | F1. This design refuses every 3xx on the 9.1 strict transport. **[pending architect R-text]**: whether R1 also covers 8.5 / `none` endpoints. This design does not extend it, to keep 8.5 rollback behavior unchanged. Also pending: whether R1 permits any proven canonicalization. This design permits none. |
| R2–R4 | Text not located. | Nothing here is attributed to them. Any point Codex believes they govern is **[pending architect R-text]**. |
| R5 | "beta.7 if queue holds" (summary only; numbering assumed). | F8. |
| A1–A7 | As Codex restated and agreed (queue; dashboard list membership; ha_call_service allowlist; no-fabrication projections; raw YAML transient-only; all 29; full vs readonly). | Cited per row. Their original architect text is likewise not located. |
| Josh decision 2026-11-06 | `ha_get_skill_guide` on 9.1 is file-only (option a). The listing and file-list tiers are unavailable, and there is no static inventory. | Ledger row 18. |
| Josh 2.5.0 requirement (brief) | 8.5.0 and 9.1 side by side; Engineering-first; rollback keeps working; release notes cover `/catalog/full` and rollback. | Sections 4–5. |

## 3. Strict selector parser bound to every MCP request

### 3.1 Upstream grammar (exact 9.1 source, S)

The upstream implementation is [`EndpointSelectors._selectors`][u-sel]:

1. It operates on the decoded Starlette route path after `rstrip("/")`.
2. It requires the path to start with `base + "/"`.
3. It splits the suffix on `/`. `readonly` is recognized only as the first suffix
   segment.
4. The admitted forms are `catalog/<full|search>`, `readonly`, and
   `readonly/catalog/<full|search>`.
5. Anything else passes through unaliased: the base path itself, or a 404 for an
   unknown suffix.
6. Matching is case-sensitive.
7. Percent-encoded separators are decoded before matching.

In the Phase 1 capture configuration the default endpoint was search (11
tools), and it can be full when search is disabled, so a catalog fingerprint
alone cannot prove the selector.

### 3.2 Engineering classifier [IC]

`classify_upstream_selector(url) -> SelectorClass` lives beside
`parse_upstream_dashboard_endpoint`
([`configuration.py:98-167`][e-cfg98]) and runs after that parser has accepted
the URL. It inspects only `urlsplit(url).path`. Query, userinfo and host are
never selector inputs, but they remain part of the bound URL.

1. **Raw classification.** Split the raw path on `/` and evaluate from the
   right:
   - `…/readonly/catalog/full` is `readonly_full`;
   - `…/readonly/catalog/search` is `readonly_search`;
   - `…/catalog/full` is `full`;
   - `…/catalog/search` is `search`;
   - `…/catalog/<other>` is `unknown_catalog`;
   - `…/readonly` is `readonly`;
   - anything else is `none`.
2. **Normalized classification.** Percent-decode, remove dot segments (RFC 3986
   §5.2.4), collapse empty segments, `rstrip("/")`, case-fold, then classify
   again.
3. **Ambiguity.** If the two classifications differ, the class is `ambiguous`.
   The same applies when any of the last four raw segments contains `%`, is `.`
   or `..`, or is empty. This catches:
   - `…/catalog%2Ffull`;
   - `…/catalog/./full`;
   - `…/x/../catalog/full`;
   - `…/catalog/full/`;
   - `…/catalog//full`;
   - `…/Catalog/Full`.
4. **Admitted classes.** Only `none` and `full` are admitted classes. Every other
   class is a configuration refusal before any session opens. This applies to
   all versions, because any such URL is either an unadmitted 9.1 mode or a 404
   on 8.5.
5. **Version table.** `REQUIRED_CATALOG_SELECTOR` is compiled as
   `{"9.1.0": "full"}`, and every other admitted version requires `none`.

**Known false refusal (fail-closed, accepted [IC]).** A private base path whose
own last segments look like a selector, such as one ending in `readonly`, or one
with percent-encoding near the end, is refused. The operator fix is a different
base path. Note: the existing parser already counts any segment of 8 or more
characters, such as `readonly`, as a credential segment for redaction
([`configuration.py:122-131`][e-cfg122]).

### 3.3 Binding to every request [IC]

**Strict transport.** `StrictBoundMcpTransport(httpx.AsyncBaseTransport)`
generalizes the existing dashboard-analysis fence
([`dashboard_analysis.py:257-317`][e-da257]). It is constructed per SDK session
from an immutable endpoint snapshot `(url, selector_class, generation)` and
installed through `streamablehttp_client(..., httpx_client_factory=…)`
([SDK `streamable_http.py:711-746`][sdk-legacy]). The factory builds
`httpx.AsyncClient(transport=strict, trust_env=False, follow_redirects=False,
max_redirects=0)`.

The transport applies three checks to each request:

- **URL.** `request.url` must equal the bound `httpx.URL` exactly. A mismatch
  raises `UpstreamEndpointRefused("upstream_endpoint_rejected")` before any byte
  is sent.
- **Method.** The method must be POST, GET or DELETE. These are the SDK's
  initialize, notification, list, call, GET-stream and session-cleanup methods
  ([SDK `streamable_http.py:604`][sdk-delete] for DELETE).
- **Status.** A response with status `300 ≤ s < 400` is closed without reading
  its body or `Location`, and raises
  `UpstreamEndpointRefused("upstream_redirect_refused")`. The SDK therefore
  never reaches `_raise_for_unfollowed_redirect` or its Location-bearing
  message ([`:79-94`][sdk-unfollowed]).

**Belt and braces.** `max_redirects=0` makes the SDK loop at
[`_httpx_utils.py:130-145`][sdk-stream] stop after the first send even without
the 3xx refusal.

**Scope.** These consumers all use it:

| Consumer | Location |
|---|---|
| Read gateway | `McpReadGatewayTransport._run_isolated` and `_run_worker` ([`upstream_read.py:231`][e-ur231], [`:292`][e-ur292]) |
| Dashboard transport | [`mcp.py:331`][e-mcp331] |
| Dashboard analysis | [`dashboard_analysis.py:455`][e-da455] |
| Typed power, fan and helper providers, platform, lifecycle and backup providers | Through `McpReadGatewayTransport`. The parser call sites are [`application.py:124,244,708`][e-app], [`upstream_fan.py:41`][e-uf41], [`upstream_dashboard.py:380`][e-ud380], [`upstream_read_gateway.py:1126`][e-g1126], [`operational_lifecycle.py:269`][e-ol269], [`operational_backup.py:148`][e-ob148]. |

The transports receive the endpoint snapshot, not a bare `url` string.

**Admission check after initialize.** One shared function,
`require_selector_agreement(release_version, snapshot, transport_kind)`, is
called from every identity point: [`_validate_identity`][e-g2773] (protocol
check at [`:2824`][e-g2824]), the dashboard handshake ([`mcp.py:346`][e-mcp346])
and the analysis handshake ([`dashboard_analysis.py:463`][e-da463]). It refuses
`upstream_catalog_mode_unadmitted` unless one of these holds:

- the version is `9.1.0`, the selector is `full`, and the transport is strict;
- the version is not `9.1.0` and the selector is `none`.

This runs before list, so no tool is listed or dispatched on a mismatch. It runs
again before every tools/call, because a retained session revalidates its
snapshot generation (see the retained-session row below).

**Per lifecycle phase:**

| Phase | Binding |
|---|---|
| initialize | Strict transport; bound URL; 3xx refused. Then `require_selector_agreement`, then protocol `2025-03-26`. |
| `notifications/initialized` and any notification | Same transport and bound URL. |
| `tools/list` and every pagination cursor page | Same transport. The order fingerprint is computed over the concatenated `pages[*].tools` order ([`upstream_tool_policy.py:73`][e-p73], check [`:988-994`][e-p988]). |
| `tools/call` | Same transport. The existing per-call catalog validator and `before_dispatch` stay in place. |
| GET stream (if opened by the SDK) | Same transport. A 3xx is refused at transport level, so the SDK's URL-bearing warning at `streamable_http.py:295` is never reached. The existing logger silencing ([`upstream_read.py:104-107`][e-ur104]) is kept anyway. |
| Retained worker / reconnect | Each new SDK session builds a new strict transport from the *current* snapshot. A retained worker whose snapshot generation differs from the current configuration is closed before reuse. The existing authority and revocation checks are unchanged. |
| Session DELETE cleanup | Same transport and bound URL. A 3xx during cleanup is refused and logged by bounded reason only. Cleanup failure never changes a read or write outcome already recorded. |

**Echo rule (HAMCP-288 parallel item).** No reason, exception, audit field,
capability text or log line produced by this design carries the configured URL,
host, path, query or any selector token other than the literal expected value
`/catalog/full`. Refusal reason codes are closed strings:

- `upstream_redirect_refused`;
- `upstream_endpoint_rejected`;
- `upstream_catalog_mode_unadmitted`;
- `upstream_catalog_selector_invalid`.

Existing `secret_values` redaction stays the second line of defense. The
classifier result is never serialized.

### 3.4 Order fingerprint and descriptors

The 9.1 order fingerprint is the existing `schema_fingerprint` over the observed
name order across all pages. It uses the same optional-name treatment the 8.5
optional YAML admission uses today (HAMCP-162). Per-descriptor, annotation,
output-schema, duplicate-name and count checks are compiled from the Phase 1
`catalogs/full-1.json` and `full-2.json` captures (C), never from 8.5 or 8.6.
All 82 rows in [`provider-delta.json`](evidence/hamcp319/phase1/receipts/provider-delta.json)
receive an explicit disposition, including prohibited and quarantined names.

## 4. The `/catalog/full` option

**Operator configuration (Stage B, after the app is updated to 9.1.0).** Append
exactly `/catalog/full` to the existing `upstream_dashboard_mcp_url`, with no
trailing slash, encoding or case change. Leave the private base path, scheme,
host and port as they are. The ha-mcp app options must match the Phase 1
captured configuration:

- app mode;
- tool search enabled;
- filesystem tools enabled;
- global readonly **false**;
- component 2.2.1 tools-only for the optional YAML read.

**What Engineering verifies:**

1. At configuration load, the existing parser plus the selector classifier
   check that the class is `full` or `none`.
2. At initialize, over the strict transport:
   - `serverInfo.name`;
   - `serverInfo.version == "9.1.0"`;
   - protocol `2025-03-26`;
   - selector agreement.
3. At list, over the same transport:
   - the exact 82-name order fingerprint;
   - per-descriptor, annotation and output-schema fingerprints;
   - no duplicates or missing names.
4. At call, the per-call catalog validator, the exact argument construction,
   and the version-keyed adapters.

**Failure modes.** All are fail-closed, with bounded reasons and no URL or path
echo:

| Situation | Result |
|---|---|
| 9.1 app, suffix not yet appended (selector `none`) | `upstream_catalog_mode_unadmitted`, naming the expected `/catalog/full`. Delegated reads, typed controls and dashboard operations are unavailable. Engineering-native capabilities continue. |
| 9.1 with `/readonly`, `/readonly/catalog/full`, `/catalog/search` or an unknown or ambiguous selector | `upstream_catalog_selector_invalid` at configuration, before any session. |
| 9.1 with `full` but app options differ (for example global readonly gives the 40-tool catalog) | The existing catalog-mismatch refusal. Stock reads are not admitted on an uncaptured catalog. |
| 9.1 with filesystem tools disabled | **UNKNOWN until implemented.** Parity requires this to behave like 8.5 with filesystem disabled: stock reads admitted, optional YAML unavailable. The expected catalog is S, derived by removing the five filesystem-gated names ([`config_settings.py:678-680`][u91-fs], [`tools_yaml_read.py:651`][u91-yfs]). Until an exact-image case executes it, it is labeled S. If the order fingerprint cannot admit it without weakening the optional boundary, this is reported as a parity blocker, not silently held. |
| Upstream answers any request with 3xx | `upstream_redirect_refused`; nothing is followed and `Location` is not read. |
| Request URL differs from the bound URL (SDK or library drift) | `upstream_endpoint_rejected` before send. |
| 8.5.0 app with `/catalog/full` (rollback without removing the suffix) | 8.5 returns 404 at initialize, which is the existing bounded HTTP failure. If anything answers as 8.5.0 on that path, selector agreement refuses it. |
| Negotiated protocol other than `2025-03-26` | Existing `unsupported_protocol_version`. |

## 5. Side-by-side admission and rollback

**Registry.** `upstream_release_registry.json` keeps the 8.5.0 entry byte for
byte and adds a 9.1.0 entry. Every version-keyed table that has an 8.5.0 or
8.6.0 row receives an **explicit** 9.1.0 row or an explicit hold. Nothing is
defaulted or inferred (the HAMCP-184 rule). Candidates enumerated at the base:

| Area | Files |
|---|---|
| Tool policy and order | `upstream_tool_policy.py` |
| Gateway and contracts | `providers/upstream_read_gateway.py`, `providers/upstream_contracts.py` |
| Platform, blueprint and YAML | `providers/upstream_platform_reads.py`, `providers/upstream_blueprint.py`, `providers/upstream_yaml_reader.py` (the optional disposition table) |
| Dashboard | `f3_dashboard/{atomicity,constants,provider,raw_evidence}.py`, `providers/contracts/upstream_dashboard_builtin_attestations.json` |
| Typed controls | `fan/contracts.py`, `power/contracts.py`, `helper_value/contracts.py` |
| Other version-keyed contracts | `history_slices/contracts.py`, `integration_inspection/contracts.py`, `ha_mcp_readmission/ha_mcp.py` |
| Readmission and capabilities | `ha_core_readmission/routes.py`, `capabilities.py` |

**Core readmission note (S, U for behavior).** Device-dependent delegated reads
pass only for adapter versions bound in [`ha_core_readmission/routes.py:607-627`][e-cr607].
8.5.0 and 8.6.0 are binary-owned there, not new Core authority. 9.1.0 gets the
same binary-owned binding **only** with tests proving that 9.1 device enrichment
preserves child-device and effective-area semantics
([`tools_registry.py:774`][u91-device]). Signed Core probe fingerprints are
unchanged. If the proof fails, the reads return the existing
`ha_mcp_child_device_contract_unproven` refusal, and that is reported as a
parity blocker.

**Stages [JR]:**

| Stage | State | Expected behavior |
|---|---|---|
| A | Engineering 2.4.2-beta.7 deployed; app still 8.5.0; URL unchanged (`none`) | Identical to today. The 8.5 transport, adapters and refusals are unchanged. The 9.1 entry is dormant. |
| B1 | Josh updates the app to 9.1.0; URL still `none` | Delegated surfaces refuse with `upstream_catalog_mode_unadmitted`, naming `/catalog/full`. No partial 9.1 surface is exposed. |
| B2 | Operator appends `/catalog/full` | Full parity surface on 9.1 through the strict transport. |
| Rollback | App back to 8.5.0, **and** suffix removed | Returns to the Stage A state. Removing the suffix is required. If the suffix is left, 8.5 returns 404 and the delegated surface stays fail-closed. |
| Engineering rollback | Engineering returned to beta.6 or earlier while the app is 9.1 | Older Engineering has no 9.1 entry and refuses 9.1, which is existing behavior. The operator must roll the app back to 8.5.0 and remove the suffix first. |

The release notes must state the Stage B suffix and both rollback orders. That
text is written when the base version materializes; it is not written here.

**Durable records across rollback.** Task, intent and audit records carry the
upstream version and adapter identifiers already. A 9.1-written record that is
read after rollback to 8.5 is displayed and reconciled by its recorded version
and is never re-dispatched. The tests include this.

## 6. Per-surface designs

### 6.1 History statistics projection [IC]

The `ha-mcp-9.1.0-history-statistics-v1` projection produces, for each
requested entity in order:

```
{entity_id, period, statistics:[{start, <type>…}], unit_of_measurement, <pagination>}
```

- Each row's `<type>` keys are the requested `statistic_types`, or 8.5's default
  `[mean, min, max, sum, state, change]` ([8.5 `:1180`][u85-stat-default]).
  They are taken from the 9.1 raw row **only when present**.
- `unit_of_measurement` comes from 9.1's per-entity `unit_of_measurement`.
- 9.1 `end`, `unit_source`, `unit_reason` and `statistics_metadata` are dropped
  as fields 8.5 never returned.
- A requested type absent from a row stays absent, as in 8.5.
- The top level keeps `source`, `period_type`, `time_range`, `statistic_types`,
  `query_params` and the empty-entities warning.
- 9.1 unit warnings are retained as bounded warnings (F3).
- Engineering keeps the invalid-period refusal that 8.5 performed client-side,
  because 9.1 removed it.

### 6.2 Typed writes: outcome matrix [IC]

Arguments are fixed per [AC 3] (rule 6). The public status names below are the
existing ones; no new status is introduced.

| Injected point | Power | Fan | Helper | Retry |
|---|---|---|---|---|
| Before durable intent (validation, authority, catalog, `ws_command` or extra key) | refused; not dispatched | refused; not dispatched | refused; not dispatched | n/a |
| After intent, proven pre-send local refusal ([`upstream_fan.py:94`][e-uf94]) | `call_not_sent` (kept) | `call_not_sent` (kept) | `call_not_sent` (kept) | none |
| Cancellation or timeout during send or before reply | `dispatch_indeterminate` | `dispatch_indeterminate` ([`executor.py:357`][e-ex357]) | `changed_unverified` ([`helper_value/service.py:87`][e-hv87]) | **none** |
| Upstream `success:true, partial:true` (timeout envelope) | observe, then a verified result or `dispatch_indeterminate` | same | observe, then verified or `changed_unverified` | none |
| Non-admin `result: []` / `{changed_states: []}` | observe | observe | observe | none |
| Observation timeout | uncertain (existing status) | uncertain | `changed_unverified` | none |
| Exception group containing `CancelledError` | treated as cancellation | same | same | none |
| Client task cancelled | no immediate normal response is guaranteed; durable task, status and audit reconciliation report the outcome ([`fan/service.py:225`][e-fs225]) | same | same | none |

Only verified readback resolves uncertainty to success. Ambiguous delivery never
sets `provider_dispatch_occurred:false` or `FAILED_PRE_DISPATCH`. Reads keep
their current timeout and cancel semantics.

**Positive coverage.** An exact owner-authorized power, fan or helper operation
on 9.1 still succeeds with verified readback, including when its disclosed
consequence is high.

### 6.3 Dashboard model [IC on AC 2 / F4]

| Fact | Established by | Not established by |
|---|---|---|
| Identity exists | A positive entry in a complete, positively decoded registry list; built-in default and aliases by explicit compiled rule; a successful get, including `stored_config:false` | `[]` from a collapsed list ([`:1796`][u91-dlist] uses `or []`); a `None` fetch result; config absence |
| Identity absent | `read_failure_code`'s exact unknown-identifier mapping to `RESOURCE_NOT_FOUND` ([`dashboard_read_errors.py:64-71`][u91-dre]), under a compiled version-bound contract; or absence from a list proven complete for non-default targets | Substring heuristics beyond that contract; `config: None` |
| Saved config readable | `config` is a dict and its hash is derived from it ([`upstream_dashboard.py:582`][e-ud582]) | `stored_config:false`, recovery mode, missing YAML file |
| Write committed | Upstream `write_committed: true` **and** Engineering readback and hash verification | `write_committed: None` / `reason: write_outcome_unknown` ([`:3339-3400`][u91-save]); a cancelled save |

**Consequences:**

- **Planning.** Plan, preflight, atomicity and attestation require "config
  readable". If it is missing, set, patch and create are refused. Absence does
  not unlock create.
- **Metadata before config.** A metadata change followed by a config failure is
  reported as a partial and uncertain outcome.
- **Existing protections.** BPS and strategy-replacement protections are
  unchanged.
- **Arguments.** `mode` and `query` stay prohibited
  ([`upstream_contracts.py:71`][e-uc71]). `describe`, screenshot,
  `python_transform` and new search forms are not admitted.

### 6.4 Optional YAML on 9.1 with 2.2.1 [IC on AC 5]

**Arguments.** Engineering sends `file=configuration.yaml`, the fixed
`yaml_path`, `include_parsed=true` and `include_content=true`. The text channel
is now requested *for the tag scan only*.

**Scanner rules:**

1. Raw text is used only as transient input to a bounded scanner. Its size cap
   matches the existing projection bound.
2. The scanner uses the YAML tokenizer **without constructors**. It never loads,
   resolves secrets, or follows includes. It distinguishes tag tokens from
   scalars and comments.
3. A tag in the 2.2.1 preserved set is not loss.
4. `!input` and any other tag set `tag_lossy: true` with `lossy_tags` drawn from
   a closed allowlist (`"!input"`) or the generic `"unknown_tag"`. Arbitrary tag
   text or URIs are never echoed.
5. Missing, truncated, oversize or untokenizable content gives
   `tag_completeness: "unavailable"`, never lossless.
6. After scanning, `content` is deleted from both the text channel and the
   structured channel before agreement checks and downstream handling.

**Contracts and admission.**

- The `_MATCH_KEYS` contract becomes version-keyed. 9.1 accepts
  `{file, yaml_path, parsed, content}` at the ingress boundary only, and the
  8.5 contract stays `{file, yaml_path, parsed}`.
- The binding is a new `OPTIONAL_TOOL_BINDINGS[("9.1.0", "ha_config_get_yaml")]`
  with an explicit `OPTIONAL_TOOL_DISPOSITIONS["9.1.0"]`. The stock policy still
  cannot name optional tools.
- All four file siblings stay prohibited; captured descriptors never grant
  sibling reachability.
- `!secret` stays unresolved (C in Phase 1). The fixed file and key projection
  stays, and no recursive completeness is claimed.

## 7. Per-tool ledger (29 rows)

Key: **P** parity (bounded attributed pass-through with compiled completeness);
**PJ** parity through a compiled projection or input translation; **FW** fixed
wrapper; **PD** parity with disclosed unavailable fields (no fabrication);
**H** hold. 9.1 citations are at `d2e3f4b0`.

| # | Tool | Disposition | Reason / required proof |
|---|---|---|---|
| 1 | ha_config_get_automation ([`:409`][l1]) | PJ | The public `identifier` is translated to `entity_id`. The added `entity`, `validation` and `references` are passed through bounded. Missing-automation error context `identifier`→`automation_id` is mapped by the 9.1 error adapter (rule 9). |
| 2 | ha_config_get_calendar_events ([`:167`][l2]) | P | Defaults unchanged. Bounded errors use the new descriptor fingerprint. |
| 3 | ha_config_get_category ([`:45`][l3]) | P | Body is code-identical. Empty, not-found and refusal cases are covered. |
| 4 | ha_config_get_label ([`:592`][l4]) | P | Body is code-identical. Exact list shape and not-found are covered. |
| 5 | ha_config_get_scene ([`:306`][l5]) | PJ | Engineering sends only `scene_id`, which is required. `query`, `search_in_config`, `limit` and `offset` are never sent, so discovery cannot run. |
| 6 | ha_config_get_script ([`:119`][l6]) | P | Identity normalization. The changed missing-script context is handled by the error adapter, and the downstream script consumer is preserved. |
| 7 | ha_config_list_dashboard_resources ([`:175`][l7]) | PJ | `include_hash` is omitted. `resource_mode` and the yaml `note` are passed through, and the dynamic `by_type` is accounted for. No writes. |
| 8 | ha_config_list_groups ([`:110`][l8]) | PD | `all` and `order` are no longer guaranteed. When absent they are disclosed per record as unavailable, never synthesized. Counts and paging are kept. |
| 9 | ha_config_list_helpers ([`:91`][l9]) | PJ | The public `helper_type` enum is kept. `describe=False` is pinned and `menu_choice`/`helper_id` are left unset. 2.2.1 capability branches and errors are S. |
| 10 | ha_eval_template ([`:80`][u91-eval]) | PD | Render only. `condition=None`, `variables=None` and `strict=False` are pinned, with integer `timeout` in 1–60. `request_id` and `evaluation_time` are reported unavailable [AC 4]. The 9.1 error codes (`VALIDATION_FAILED` / `TIMEOUT_OPERATION`) are mapped to the existing public errors. No condition executor. |
| 11 | ha_get_automation_traces ([`:44`][l11]) | P | Defaults unchanged. Order, offset, dedup, detail bounds and failures are covered. |
| 12 | ha_get_device ([`:774`][l12]) | P | Composite enrichment relocated. Core child-device binding per section 5. Enrichment edge cases are U until executed. |
| 13 | ha_get_entity ([`:1794`][l13]) | P | `uses_device_name` is additive. Error contract per rule 9. No registry mutation. |
| 14 | ha_get_entity_exposure ([`:206`][l14]) | P | Body is code-identical. No exposure writes. |
| 15 | ha_get_hacs_info ([`:109`][l15]) | **H** | Held on Core 2026.11 with zero probe; Phase 3 (HAMCP-290). Only the seam is named: a future version-keyed read contract. Source and Marketplace captures do not authorize reachability. |
| 16 | ha_get_history ([`:209`][l16]) | PJ | State rows use the 9.1 parser (rule 3). Statistics use the section 6.1 projection. Pinned arguments per rule 3. Absent attributes are unavailable, not `{}`. Warnings are kept. |
| 17 | ha_get_overview ([`:431`][l17]) | PJ | Selected fields are preserved. The always-added `mcp_client` block is dropped by projection, as it describes Engineering's own internal session, not household data. The changed `settings_url` and `tool_discovery` text are not exposed. Completeness is F3. |
| 18 | ha_get_skill_guide ([`:935`][u91-guide]) | PD [JD] | File-only, like the 8.6 adapter. The `{}` and `skill`-only tiers return a bounded "unavailable on this upstream version" refusal, with no static inventory. `skill+file` maps to `file` for the one bundled skill, and other skills are refused. The strict-BPS acknowledgement line is handled as in 8.6. |
| 19 | ha_get_state ([`:712`][l19]) | P | Visible state is useful. A removed attribute mapping is reported withheld or partial (F3). Hidden-ID refusal is kept, and typed state constraints still refuse on incomplete evidence. |
| 20 | ha_get_todo ([`:309`][l20]) | P | Read only. The public scalar `status` is kept. Retrieval through `todo.get_items` keeps the reviewed read semantics. No todo mutation. |
| 21 | ha_get_zone ([`:170`][l21]) | P | `entity_id` and `icon` are additive. Additional legacy YAML or home rows must not drop the 8.5 rows or broaden actions. |
| 22 | ha_list_floors_areas ([`:142`][l22]) | P | Body is code-identical. No registry writes. |
| 23 | ha_list_services ([`:65`][l23]) | P | Only the annotation changed. Typed service discovery stays exact-domain and is validated independently. |
| 24 | ha_search ([`:61`][l24]) | P | Bounded search only. Visibility omissions are disclosed and empty `warnings` normalized. All-legs failure is a failure, not empty. Component branches are S. The closed schema is kept. |
| 25 | ha_get_blueprint ([`:164`][l25]) | FW | Compiled list/get wrapper over the mixed `ha_manage_blueprints` ([`upstream_blueprint.py:52`][e-ub52]). No import, save, delete or substitute. Full-content provenance and partial data are covered. |
| 26 | ha_get_app ([`:3694`][l26]) | FW | Fixed installed-app projection and stats cap ([`upstream_platform_reads.py:269`][e-pr269]). No store, management or source widening. |
| 27 | ha_get_integration ([`:410`][l27]) | FW | Fixed query window, `options=false`, bounded projection. The new diagnostics path, offset, limit and schema options are never sent. |
| 28 | ha_get_system_health ([`:481`][l28]) | FW | Existing fixed sections only. New diagnostics parameters are inaccessible. |
| 29 | ha_config_get_yaml ([`:484`][l29]) | FW (optional) | Section 6.4. |

**Outside the 29.** These keep their own governed bindings, rebound per version
with success and refusal tests:

- the governed dashboard surfaces: list, get, planning, write, verification,
  analysis and BPS (reads through the 8.6 guide special case at
  [`mcp.py:361`][e-mcp361]; for 9.1 the same file-only form, per [JD]);
- typed power, fan and helper.

Held backup and lifecycle surfaces, quarantined raw tools, generic
`ha_call_service`, filesystem siblings, developer and issue-report tools all
remain unavailable.

## 8. Test plan

All tests are offline and deterministic, with synthetic loopback peers and
`.artifacts/` scratch. Each item states its evidence class. Section 8.1 holds the
negatives Codex required.

### 8.1 Required negatives

1. **Redirect.** On a strict `full` endpoint, inject 301, 302, 303, 307 and 308
   at each phase: initialize, initialized notification, each list page,
   tools/call, the GET stream and DELETE cleanup. The targets are three
   same-origin mode changes (`/default`, `/readonly/catalog/full`,
   `/catalog/search`), a same-path canonicalization, and a cross-origin target.
   - Assert `upstream_redirect_refused` and zero follow-up requests.
   - Assert no tools/call reaches the peer.
   - Assert no Location, URL, host or path appears in results, exceptions,
     audit, logs or capability text (canary-scanned).
   - **Mutation:** removing the 3xx refusal, or setting `max_redirects>0`, fails
     the test.
2. **`ws_command` forwarding refusal.** On every typed path (power, fan, helper)
   and every gateway path, supply `ws_command`, `result_fields`,
   `result_attribute_keys`, `wait:true`, `return_response:true`, `verbose:true`,
   extra targets and unknown keys.
   - Assert refusal before dispatch, with zero calls at the peer.
   - Assert the exact constructed key/value set on the positive path.
3. **get_dashboard "No config found" fails closed.** Cover `stored_config:false`
   with `config: None`, an empty-dict config, default absent from the list, a
   list collapsed to `[]`, a `None` fetch, a malformed or truncated list,
   recovery mode or missing YAML, an alias mismatch, and stale membership.
   - Assert set, patch and create are refused.
   - Assert no hash is derived from a non-dict config.
   - Assert existence is not decided absent from `[]`.
   - Assert positive governed operations on a readable dashboard still pass.
4. **Cancellation gives `changed_unverified` with no retry.** For each family,
   inject cancellation or timeout before intent, after intent before send,
   during send, after upstream application before reply, and during
   observation. Use real task cancellation, not only a mocked receipt.
   - Assert no redispatch (peer call count ≤ 1).
   - Assert durable intent survives.
   - Assert the status per the section 6.2 matrix, with the helper reporting
     `changed_unverified`.
   - Assert a proven `call_not_sent` stays distinguishable.
   - Assert subsequent status and recovery reconcile.
5. **Non-admin empty `changed_states`.** `result: []` and
   `{changed_states: [], service_response}` go to observation.
   - With matching readback, the result is verified success.
   - With non-matching or absent readback, the result is uncertain.
   - The result is never `call_not_sent` or failure.

### 8.2 Selector and transport

- **Classifier table.** Run every form from section 3.2 at the end of a
  synthetic base: `none`, `full`, `search`, `readonly`, `readonly/catalog/full`,
  `readonly/catalog/search`, `catalog/other`, and encoded, dot, empty, case and
  trailing-slash variants. Assert the classes and the configuration refusal.
  The synthetic base itself never appears in output.
- **Selector agreement.** 9.1 + `none`, 9.1 + `full` on a non-strict transport,
  and 8.5 + `full` are all refused before list. 9.1 + `full` strict and
  8.5 + `none` are admitted (positive).
- **Uniform catalog per selector.** An identical 82-tool catalog served on each
  unadmitted selector is still refused.
- **Order across pages.** The order is taken across multiple pages, and pages
  re-sorted alphabetically are refused. Missing, duplicate and changed
  descriptors and annotations are refused.
- **Retained session.** A configuration generation change closes the retained
  worker. The reconnect uses the new snapshot, and a stale snapshot never
  dispatches.
- **URL drift.** A request whose URL differs from the bound URL, simulating SDK
  drift, gives `upstream_endpoint_rejected` before send.
- **AST check (supplementary).** Every `streamablehttp_client(` call in the
  package passes the shared strict factory, or is proven to be on the `none`
  path.

### 8.3 Parity and completeness

- **Two-version calls.** For each of the 29 rows, 8.5-shaped public calls run
  against both 8.5 and 9.1 fixtures. Each row asserts its positive and refusal
  dispositions, the absence of fabricated fields, and unavailable disclosure.
- **Skill guide.** The `{}`, `skill` and `skill+file` forms produce the [JD]
  dispositions.
- **History.** Statistics selection, order, offset, missing types and units;
  history-slices parsing of expanded rows with and without `attributes`.
- **F3 replays.** Replay the hidden-ID, nested-visible-omission, empty
  hidden-search, malformed or last-good visibility and registry-unavailable
  cases from the Phase 1 receipts, using JSON pointers. Assert:
  - useful data stays available;
  - omissions are disclosed;
  - no hidden value is recovered;
  - typed actions refuse on incomplete state.
- **YAML (section 6.4).**
  - Inputs: unresolved `!secret` and `!env_var`; each preserved include tag; an
    actual `!input`; `!input` inside a quoted string or a comment; tag-URI and
    arbitrary-tag canaries; absent, malformed and oversize content; duplicate
    JSON keys; text/structured mismatch.
  - Every response, error, audit and log sink is scanned for raw YAML and
    canaries.
  - Component missing or disabled; package, file and glob refusal; unchanged
    8.5 behavior; altered optional descriptors cannot grant a sibling.
- **Rollback.** A 9.1-written intent, task or audit record is read under 8.5
  configuration, displayed by its recorded version, and never re-dispatched.

### 8.4 Fixture provenance

- A fixture that cites a real but unrelated evidence hash or JSON pointer fails
  validation.
- S fixtures carry file:line and are counted separately.
- **Mutations:**
  - drop the 9.1 global admission;
  - drop each typed and provider route row in turn (power, fan, helper,
    dashboard, platform, blueprint, YAML, history-slices, error adapter, Core
    child-device binding);
  - remove selector agreement;
  - remove the 3xx refusal.

  Each must fail a named test.

### 8.5 Validation sequence

1. Focused suites during implementation.
2. Exact-image lane cases for the S rows on amd64, recording image, manifest,
   config, endpoint class and protocol, plus executed counts and skips. arm64
   remains U unless executed.
3. One final Evidence run (`-Parallel 4`, pinned `-BaseRef`, every
   `-AuthorizedProtectedPath`) under the host rule, when next in line.

None of these was run for this design revision.

## 9. UNKNOWNs carried forward

1. Whether HA keeps executing a service call after the MCP client cancels (not
   determinable from source; Phase 1 does not prove it).
2. Whether production 8.5 relies on any same-origin redirect. This is why the
   `none` path transport is unchanged.
3. The behavior of the 9.1 app with filesystem tools disabled (S until
   executed).
4. 9.1 device enrichment edge cases and the Core child-device binding (section
   5).
5. Component 2.2.1 capability branches (`template_diagnose`, `helpers_list`,
   `search_unified`, `find_yaml_key` absent), as exercised through 9.1.
6. The exact 9.1 error envelopes (rule 9). They are S until the exact-image lane
   executes them.
7. arm64 and standalone runtime behavior. Only amd64 app evidence is C.
8. The full text of R1–R5 and the original text of A1–A7.
9. The literal-key differences in `set_dashboard` other than `reason`
   (`content`, `favorite_entities`, `features`), which are not traced.
10. The production operator's current ha-mcp app options.

## 10. Non-goals

- **Phase 3.** Marketplace / `ha_get_hacs_info` restoration (HAMCP-290).
- **No new authority.** No new Core capability, profile or signing scope;
  seq 16/17 unchanged.
- **Component.** Component 9.1; any component other than 2.2.1 tools-only.
- **No new public surface.** Dashboard search, `describe`, screenshots,
  `python_transform` widening or any new public dashboard operation.
- **HAMCP-288 and app discovery.** HAMCP-288 secret-path logging mitigation is a
  parallel item, and 9.1 does not mitigate it. App-discovery configuration
  changes are excluded; the existing Ignore decision on the discovery prompt
  stays.
- **No redesign.** No general SDK or protocol redesign; strict transport is
  version-scoped. No change to 8.5 transport behavior.
- **Out of this item PR.** Version changes, release declarations, publication,
  deployment, and any live Home Assistant access.
- **Other configurations.** Supporting ha-mcp app configurations other than the
  captured one, beyond the filesystem-disabled parity case in section 4.

## 11. Open decisions

- **Josh:** none blocking. These disclosed parity differences are already
  covered by [JD] or [AC 4]:
  - skill-guide listing and file-list tiers unavailable;
  - eval-template `request_id` and `evaluation_time` unavailable;
  - groups `all`/`order` unavailable when absent;
  - dashboard absence unavailable when the registry list collapses.

  The release notes must state them.
- **Architect [pending architect R-text]:**
  - R1 scope beyond the 9.1 endpoint;
  - whether any canonicalization redirect may be followed (this design: none);
  - anything in R2–R4.

[u91]: https://github.com/homeassistant-ai/ha-mcp/tree/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33
[u85]: https://github.com/homeassistant-ai/ha-mcp/tree/311d6dc273fb4e9a5b8cde0de15f69472a64fe44
[c221]: https://github.com/homeassistant-ai/ha-mcp/tree/fc54437a804858732e4bc927add98e202d879a09/custom_components/ha_mcp_tools
[c-yaml42]: https://github.com/homeassistant-ai/ha-mcp/blob/fc54437a804858732e4bc927add98e202d879a09/custom_components/ha_mcp_tools/yaml_rt.py#L42
[c-yaml192]: https://github.com/homeassistant-ai/ha-mcp/blob/fc54437a804858732e4bc927add98e202d879a09/custom_components/ha_mcp_tools/yaml_rt.py#L192
[u-sel]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/http_transport.py#L122-L172
[u-cat]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tool_catalog.py#L56-L60
[u91-guide]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/server.py#L935
[u85-guide]: https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/server.py#L1679
[u85-stat]: https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_history.py#L1077-L1115
[u85-stat-default]: https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_history.py#L1180
[u91-stat]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_history.py#L989-L1040
[u91-eval]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_utility.py#L80
[u85-eval]: https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_utility.py#L30-L61
[u91-hoist]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/response_helpers.py#L392
[u91-hist]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/history_response.py#L49
[u91-dre]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/dashboard_read_errors.py#L29-L71
[u91-dlist]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_config_dashboards.py#L1796
[u91-dfetch]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_config_dashboards.py#L1005
[u91-lovelace]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_config_dashboards.py#L3066
[u91-save]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_config_dashboards.py#L3339-L3400
[u91-partial]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_service.py#L855
[u91-ws]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_service.py#L679-L802
[u91-nonadmin]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/client/rest_client.py#L681
[u91-fs]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/config_settings.py#L678-L680
[u91-yfs]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_yaml_read.py#L651
[u91-device]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_registry.py#L774
[l1]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_config_automations.py#L409
[l2]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_calendar.py#L167
[l3]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_categories.py#L45
[l4]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_labels.py#L592
[l5]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_config_scenes.py#L306
[l6]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_config_scripts.py#L119
[l7]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_resources.py#L175
[l8]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_groups.py#L110
[l9]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_config_helpers.py#L91
[l11]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_traces.py#L44
[l12]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_registry.py#L774
[l13]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_entities.py#L1794
[l14]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_voice_assistant.py#L206
[l15]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_hacs.py#L109
[l16]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_history.py#L209
[l17]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_search.py#L431
[l19]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_search.py#L712
[l20]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_todo.py#L309
[l21]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_zones.py#L170
[l22]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_areas.py#L142
[l23]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_services.py#L65
[l24]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_search.py#L61
[l25]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_blueprints.py#L164
[l26]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_addons.py#L3694
[l27]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_integrations.py#L410
[l28]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_system.py#L481
[l29]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_yaml_read.py#L484
[sdk-factory]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/shared/_httpx_utils.py#L33-L60
[sdk-stream]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/shared/_httpx_utils.py#L112-L145
[sdk-unfollowed]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L79-L94
[sdk-delete]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L604
[sdk-legacy]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L711-L746
[e-cfg98]: ../hass_mcp_engineering_beta/ha_mcp_engineering/configuration.py#L98-L167
[e-cfg122]: ../hass_mcp_engineering_beta/ha_mcp_engineering/configuration.py#L122-L131
[e-ur104]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/upstream_read.py#L104-L107
[e-ur231]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/upstream_read.py#L231
[e-ur292]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/upstream_read.py#L292
[e-mcp331]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L331
[e-mcp346]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L346
[e-mcp361]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L361
[e-da257]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/dashboard_analysis.py#L257-L317
[e-da455]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/dashboard_analysis.py#L449-L456
[e-da463]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/dashboard_analysis.py#L463
[e-app]: ../hass_mcp_engineering_beta/ha_mcp_engineering/application.py#L124
[e-uf41]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_fan.py#L41
[e-uf94]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_fan.py#L94
[e-uf159]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_fan.py#L159
[e-ud380]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_dashboard.py#L380
[e-ud582]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_dashboard.py#L582
[e-uc71]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_contracts.py#L71
[e-g1126]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L1126
[e-g2773]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L2773
[e-g2824]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L2824
[e-g4133]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L4133
[e-g4298]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L4298
[e-g7106]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L7106
[e-ol269]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py#L269
[e-ob148]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_backup.py#L148
[e-pr269]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L269
[e-pr364]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L364
[e-ub52]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_blueprint.py#L52
[e-r86-656]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_reads_8_6.py#L656
[e-r86-1031]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_reads_8_6.py#L1031
[e-y1293]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py#L1293-L1296
[e-y1827]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_yaml_reader.py#L1827
[e-p73]: ../hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy.py#L73
[e-p988]: ../hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy.py#L988-L994
[e-p1359]: ../hass_mcp_engineering_beta/ha_mcp_engineering/upstream_tool_policy.py#L1359-L1363
[e-fa108]: ../hass_mcp_engineering_beta/ha_mcp_engineering/fan/adapter.py#L108
[e-fs225]: ../hass_mcp_engineering_beta/ha_mcp_engineering/fan/service.py#L225
[e-fc78]: ../hass_mcp_engineering_beta/ha_mcp_engineering/fan/contracts.py#L78-L85
[e-pc49]: ../hass_mcp_engineering_beta/ha_mcp_engineering/power/contracts.py#L49-L52
[e-hc167]: ../hass_mcp_engineering_beta/ha_mcp_engineering/helper_value/contracts.py#L167-L170
[e-hv87]: ../hass_mcp_engineering_beta/ha_mcp_engineering/helper_value/service.py#L87
[e-ex357]: ../hass_mcp_engineering_beta/ha_mcp_engineering/f3/executor.py#L357
[e-hs35]: ../hass_mcp_engineering_beta/ha_mcp_engineering/history_slices/contracts.py#L35
[e-hs160]: ../hass_mcp_engineering_beta/ha_mcp_engineering/history_slices/contracts.py#L160
[e-err]: ../hass_mcp_engineering_beta/ha_mcp_engineering/ha_mcp_readmission/ha_mcp.py#L137-L146
[e-cr607]: ../hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/routes.py#L607-L627
