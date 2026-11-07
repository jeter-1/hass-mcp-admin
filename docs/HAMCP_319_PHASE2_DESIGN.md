# HAMCP-319 Phase 2 design: ha-mcp 9.1.0 admission side by side with 8.5.0

Status: **round-3 design delta for Codex focused closure.** It carries the
corrections DCL1 and DCL2 to the reviewed head `622c5603`, which itself carried
DC1–DC4 against `3c06f7b1` and the verbatim R1–R5 record in section 2.

- **DCL1.** Every ha-mcp MCP connection refuses redirects, on every version
  (section 2, "JOSH DECIDED 2026-11-07"; section 3.3).
- **DCL2.** A compiled projection contract keeps Thread, Matter and other radio
  credential material out of every delegated read (section 6.5).

This document changes no runtime code, policy, registry, workflow, version or
release declaration. Implementation follows on the same pull request only after
Codex closes this design.

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
| Inputs answered | Codex design review F1–F8 with positions on A1–A7 (`~/handoffs/hass-mcp-admin-agent-codex-hamcp-319-phase2-design-review.md`); D3 addendum rules 1–9 (`~/handoffs/hass-mcp-admin-agent-hamcp-319-phase2.md`); the exact-source review (`.artifacts/hamcp319-91-source-review.md`, untracked); Codex round-2 delta review of `622c5603` (PR #385 comment 6708781521), High findings DCL1 and DCL2 |

Every decision below is tagged with its authority:

- **[IC]**: implementation choice by Claude Code, the design author. It is open to
  Codex closure and is not a product decision.
- **[JD]**: a Josh decision recorded in the HAMCP-319 register row and restated
  verbatim as [R4]. On 2026-11-06 (about 07:00 CST) Josh decided that
  `ha_get_skill_guide` on 9.1 is file-only, like the 8.6 adapter (option a). The
  listing and file-list tiers are reported unavailable, and there is no static
  inventory.
- **[JR]**: a Josh requirement for the 2.5.0 build as relayed in the task brief:
  - 8.5.0 and 9.1.0 are admitted side by side;
  - Engineering is deployed first;
  - rollback to 8.5.0 keeps working;
  - the release notes cover `/catalog/full` and rollback.

  This document relays these requirements and does not add to them.
- **[R1]–[R5]**: Josh rulings R1–R5, recorded verbatim in section 2. R4 is
  the same decision as [JD].
- **[JD-DCL1]**: JOSH DECIDED 2026-11-07, in session: redirects are refused on
  every ha-mcp MCP connection, for 8.5.0 and 9.1 alike. This revises the
  "8.5 unchanged" clause of [R2] for redirects only (section 2).
- **[JD-DCL2]**: Josh direction 2026-11-07: the Thread/Matter credential rule
  (section 2), designed in section 6.5.
- **[AC n]**: Codex position A1–A7 as accepted by [R1].
- Evidence classes follow Codex: **C** captured, **S** source-derived, **U**
  unknown or unexecuted.

## 1. Disposition table

### 1.1 Codex findings

| Finding | Revised decision | Authority | Exact citations |
|---|---|---|---|
| **F1 (High)** The configured URL was bound, not the effective endpoint. | **Two parts.** (a) The existing configuration parser is unchanged, and a strict canonical-form predicate (section 3.2) classifies an accepted URL as `strict_full` or `legacy` [R2]. The form decides **only 9.1 admission**; it never selects a transport. (b) **One** no-redirect client factory is installed through `httpx_client_factory` on **every** SDK session of **every** Engineering ha-mcp client, for every version and both forms [JD-DCL1] (section 3.3). It applies to every request type: POST initialize, notifications, list pages and tools/call, the GET stream, GET resumption and reconnection, Engineering-level reconnects and the DELETE cleanup. It does three things: refuses any request whose URL is not the bound URL exactly, refuses every 3xx response before the SDK sees it, and sets `max_redirects=0`. **No redirect is followed on any ha-mcp connection**, 8.5.0 or 9.1.0, including same-origin canonicalization. **Admission rule.** After initialize and before any list, 9.1.0 is admitted only on a `strict_full` endpoint. Every other registered version keeps its existing contract on either form (section 3.2, DC2), now on the no-redirect transport. **8.5 path.** Configuration acceptance, adapters and refusals are unchanged; redirect handling is not: a redirect that today's transport would follow is now refused with `upstream_redirect_refused` [JD-DCL1, which revises R2 "8.5 unchanged" for redirects]. **Fingerprints.** The order fingerprint comes from `pages[*].tools` order, and per-descriptor, annotation, output-schema and duplicate checks are kept. | [R2] as revised by [JD-DCL1]; transport mechanics [IC] | SDK [`_httpx_utils.py:113-145`][sdk-stream] follows same-origin 307/308 itself and ignores `follow_redirects`; [`:33-60`][sdk-factory] sets no `max_redirects` (httpx default 20). The 3xx message in [`streamable_http.py:79-94`][sdk-unfollowed] includes the Location path. Engineering SDK call sites: [`clients/upstream_read.py:231`][e-ur231], [`:292`][e-ur292], [`clients/mcp.py:331`][e-mcp331], [`clients/dashboard_analysis.py:455`][e-da455]. Existing fence: [`dashboard_analysis.py:257-317`][e-da257]. Selector grammar: [`http_transport.py:122-172`][u-sel]. Default can be full: [`tool_catalog.py:56-60`][u-cat]. |
| **F2 (High)** The parity contract was unresolved. | **Ledger.** A per-tool public input/output/error contract for all 29 rows (section 7). **Skill guide.** File-only per [R4] (section 7.1); 8.5 keeps all three tiers; the listing and file-list tiers return a bounded "unavailable on this upstream version" refusal. They are never invented and never called parity. **History statistics.** A compiled 9.1 projection reproduces the exact 8.5 filter: `start` plus requested types, with first-row unit and pagination. Row fields 8.5 never returned are dropped, and fields it returned but 9.1 omits are reported unavailable. **Eval template.** `request_id` and `evaluation_time` are reported unavailable and never synthesized. **Arguments.** Public argument refusals and fixed wrappers are preserved. Pass-through and projection are distinguished per row. | [R4] guide; [AC 4] no fabrication and statistics field selection; projection mechanics [IC] | 8.5 guide [`server.py:1679`][u85-guide]; 9.1 guide [`server.py:935`][u91-guide]; 8.6 file-only adapter [`upstream_reads_8_6.py:656`][e-r86-656], [`:1031`][e-r86-1031]; 8.5 statistics filter [`tools_history.py:1077-1115`][u85-stat], defaults [`:1180`][u85-stat-default]; 9.1 statistics [`tools_history.py:989-1040`][u91-stat]; 9.1 eval [`tools_utility.py:80`][u91-eval]; 8.5 eval fields [`tools_utility.py:30-61`][u85-eval] |
| **F3 (High)** Completeness propagation was missing. | **Treatment table.** A compiled 9.1 completeness and availability table is added to `_upstream_completeness`. It is keyed by `(9.1.0, tool)` and covers every affected read. **Disclosure.** Bounded, sanitized upstream visibility warnings set `partial` or `unavailable`. A mapping removed by visibility is reported as withheld, not as empty. Search coverage is qualified as "visible scope". **Failures.** Authentication, registry-unavailable and malformed results never become empty success. **Typed reads.** Typed state checks still refuse when required attributes are absent. | [IC]; [AC 4] | [`upstream_read_gateway.py:7106`][e-g7106] handles only blueprint, platform and search; result completeness [`:4298`][e-g4298]; 9.1 warning hoisting [`response_helpers.py:392`][u91-hoist]; Phase 1 [`visibility.json`](evidence/hamcp319/phase1/receipts/visibility.json), [`more-edges.json`](evidence/hamcp319/phase1/receipts/more-edges.json) |
| **F4 (High)** Dashboard absence was decided wrongly. | **Three facts are modeled separately:** identity existence, readable saved config, and write-outcome certainty (section 6.3). **No config.** "No config found." or `stored_config:false` means *exists, config unavailable*. It is never treated as empty or as absent. **Absence.** A complete, positively decoded registry list supports absence only for non-default targets. When the list collapses to `[]`, absence cannot be decided and is reported as unavailable. **Hashes.** No empty config or hash is ever synthesized. **Write outcomes.** Upstream `write_outcome_unknown` / `write_committed: None` maps to "outcome uncertain". **Arguments.** `mode` and `query` stay prohibited, so no dashboard search is introduced. | [AC 2] as qualified by Codex; model [IC] | [`dashboard_read_errors.py:29-71`][u91-dre]; list collapse [`tools_config_dashboards.py:1796`][u91-dlist], [`:1005`][u91-dfetch]; built-in `lovelace` [`:3066`][u91-lovelace]; save outcome [`:3339-3400`][u91-save]; Engineering config/hash requirement [`upstream_dashboard.py:582`][e-ud582]; prohibited `mode`/`query` [`upstream_contracts.py:71`][e-uc71] |
| **F5 (Medium)** The YAML tag rule was inaccurate. | **Tag set.** The exact 2.2.1 preserved set is used: `!include`, the four `!include_dir_*` tags, `!secret` and `!env_var`, all kept in source form. **Scanner.** A grammar-aware, bounded tag-token scan runs on transient `content` only. Loss is reported through a closed allowlist plus a generic `unknown_tag` marker, and arbitrary tag text is never echoed. **Raw text.** It is stripped from both channels before downstream handling and never reaches a result, log, audit or error record. **Contracts.** The 9.1 decoder and argument contract are versioned, and 8.5 behavior is unchanged. **Optional tool.** It remains binary-owned and outside the stock policy, and all four file siblings stay prohibited (section 6.4). | [AC 5]; scan design [IC] | 2.2.1 [`yaml_rt.py:42`][c-yaml42], [`:192`][c-yaml192]; [`upstream_yaml_reader.py:1293-1296`][e-y1293]; optional exclusion [`upstream_tool_policy.py:1359-1363`][e-p1359]; [`OPTIONAL_NAMES`][e-y1827] |
| **F6 (Medium)** The outcome matrix was incomplete. | **Matrix.** A per-family outcome matrix for power, fan and helper (section 6.2). **Uncertainty.** Cancellation or timeout after possible dispatch reports uncertainty (`dispatch_indeterminate` or `changed_unverified` per family), with **no retry**. **Pre-send refusal.** The proven `call_not_sent` refusal is kept. **Received responses.** `success:true, partial:true` timeout envelopes and non-admin empty `changed_states` responses count as received and go to observation. They are never proof of change. **Correction.** The earlier claim that "Phase 1 proves server continuation" is withdrawn. | [IC]; rules 7–8 | [`fan/adapter.py:108`][e-fa108]; [`f3/executor.py:357`][e-ex357]; [`helper_value/service.py:87`][e-hv87]; [`fan/service.py:225`][e-fs225]; [`upstream_fan.py:94`][e-uf94], [`:159`][e-uf159]; 9.1 [`tools_service.py:855`][u91-partial]; [`rest_client.py:681`][u91-nonadmin] |
| **F7 (Medium)** Evidence was not tied to payloads. | **Captured fixtures** carry: evidence file, sha256, RFC 6901 JSON pointer, declared normalization, and an equality check against that subtree. **Source-derived fixtures** carry the exact file and line, and stay S until an exact-image run executes them. **Counts and labels.** The schema count is corrected to **14/24** wire-identical with 8.6. `edges.json.timeout` is recorded as a class name and elapsed time, not an envelope. `yaml_component_missing` is relabeled as a successful paired read; component-missing evidence points to `component-flow.json`. **Identities.** Standalone, app and architecture identities stay separate. **Mutations** cover 9.1 *and* 8.5 global admission and every typed or provider route of both versions, each killed by a named test (section 8.4). | [IC] | Phase 1 [`provider-delta.json`](evidence/hamcp319/phase1/receipts/provider-delta.json) (77→82, 0/77 identical descriptors); [`edges.json`](evidence/hamcp319/phase1/receipts/edges.json); [`component-flow.json`](evidence/hamcp319/phase1/receipts/component-flow.json) |
| **F8 (Medium)** D9 conflicted with the release queue. | No version change in this item PR. The release base `release/2.5.0` carries the next valid beta, **2.4.2-beta.7**, materialized by HAMCP-331 at `7246f2d2` (release PR #383) and merged into this branch. This PR inherits that version and, at implementation, adds only beta.7 release-note and acceptance content. Final Evidence runs **once, on the final head of release PR #383** (`release/2.5.0` → `main`), after this PR and every other queued item are merged into `release/2.5.0`. It does not run on this item PR's head. | [R3]; [AC 1] | `scripts/release_transition.py:48-118` (beta to beta.7 validates; 2.5.0-beta.1 refuses), per the 2.5.0 build handoff |

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

## 2. Josh rulings R1–R5 (verbatim)

The rulings below are quoted exactly as supplied. Attribution: **Josh ruling,
supplied 2026-11-07.** R4 records a decision Josh made on 2026-11-06 and keeps
its own label. This section supersedes the earlier statements that the R-text
was not located and that its numbering was assumed.

> **R1** Accept F1-F8 and Codex's A1-A7 positions, incl. corrected A2 (default dashboard may be unlisted; a failed list can collapse to [] and is not proof of absence) and A6 (29 = 24 stock reads + 1 blueprint wrapper + 3 platform wrappers + optional YAML; 14/24 schemas identical to 8.6).
>
> **R2** For the 9.1 endpoint, refuse EVERY redirect on every MCP request (initialize, notifications, list pages, tools/call, reconnect, cleanup); no canonicalization allowance. Accept only a configured path in one strict canonical form ending /catalog/full; reject encoded separators, dot segments, duplicate selectors, readonly/default/search/unknown. mcp 1.30.0 follows same-origin redirects itself (stream_within_origin); mock-transport tests per request type, refusal before any dispatch. 8.5 unchanged.
>
> **R3** Version: the next valid beta (2.4.2-beta.7 via #383); Evidence once on the release PR's final head.
>
> **R4** JOSH DECIDED 2026-11-06: ha_get_skill_guide on 9.1 is file-only like 8.6; inventory and file-list tiers reported unavailable on 9.1, never synthesized; 8.5 keeps all three; no static inventory. Record whether each SKILL.md names its files. The internal dashboard-write guide read must still work, tested.
>
> **R5** One atomic parity PR for 319. Excluded: Marketplace (HAMCP-290), new Core profile/signing, component 9.1, dashboard search, HAMCP-288, app-discovery changes. A row that can't reach parity: stop and report.

R1–R5 above stay as recorded. Two later Josh decisions, both made on
2026-11-07 in answer to Codex's round-2 High findings DCL1 and DCL2, refine
them. They are restated here as relayed in the task brief; this design does not
widen or narrow them.

**DCL1. JOSH DECIDED 2026-11-07 (in session). It revises R2's "8.5
unchanged" for redirect handling.**

- Refuse redirects on **every** ha-mcp MCP connection, 8.5.0 and 9.1 alike.
- This covers every request type: initialize, the initialized notification,
  list pages, tools/call, the GET and DELETE session requests, reconnect and
  cleanup.
- It covers **every** Engineering client of ha-mcp (section 3.3 names each
  with file:line).
- There is one transport. Nothing selects a transport by URL shape, and
  nothing discriminates by version before the transport is chosen.
- The redirect is refused before the redirected request is sent. The pinned
  SDK follows same-origin redirects itself, so section 3.3 states how the 3xx
  is refused before any second request.
- The refusal reason is bounded. The private URL never appears in any output,
  error, audit record or log.
- The strict canonical `/catalog/full` selector check for 9.1 (S1–S4) stays
  exactly as designed.
- A post-deploy acceptance step proves that the production 8.5 URL connects
  with no redirect. If it does not, Engineering fails closed with a clear,
  bounded error that names the fix (section 4).
- The release notes for #383 must state this behavior change (section 5).

What it changes in R2: R2's "8.5 unchanged" still holds for configuration
acceptance (DC2, Step 0), the 8.5 adapters, the 8.5 refusals and the 8.5
public contract. It **no longer** holds for redirects: a same-origin redirect
that today's 8.5 transport follows is refused.

**DCL2. Josh direction 2026-11-07: the Thread credential rule.**

- `ha_get_device`, and any other delegated read that touches Matter or Thread,
  never passes dataset or credential material.
- This is enforced by an explicit compiled Engineering projection contract. It
  is an allowlist, not a denylist, and it keeps useful device, child-device and
  effective-area results.
- The `ha_manage_radio` dataset and management paths stay unavailable.
- There are no new upstream calls and no new Core authority.
- The regression uses synthetic canaries only, never production data.

The design is in section 6.5, ledger row 12 and section 8.3.

**Where this design depends on each ruling:**

| Ruling | Sections | Design consequence |
|---|---|---|
| R1 | 1.1 (F1–F8), 6.3 (A2), 7 (A6 count and 14/24) | All findings and A1–A7 are accepted; no finding is reopened here. |
| R2 | 3.2, 3.3, 4, 8.1 item 1, 8.2, 8.4 | Strict canonical grammar for 9.1 admission only (DC1); every accepted 8.5 value stays accepted (DC2); per-request-type mock-transport tests. Its "8.5 unchanged" clause is revised for redirects by DCL1. |
| DCL1 [JD-DCL1] | 0, 1.1 F1, 3.2, 3.3, 4, 5, 5.1, 8.1 item 1, 8.2, 8.4, 9, 10, 11 | One no-redirect transport on every ha-mcp connection of every client, every version and both forms. Bounded `upstream_redirect_refused`, with a fixed operator message and post-deploy acceptance. The S1–S4 9.1 check is unchanged. |
| DCL2 [JD-DCL2] | 6.5, 7 row 12, 7 row 28, 8.3, 8.4 | Compiled allowlist projection for `ha_get_device` on 8.5 and 9.1; a fixed credential class; `ha_manage_radio` stays unexposed; canary regression. |
| R3 | 1.1 F8, 8.5 | Version 2.4.2-beta.7 via #383. **Validation target: one Evidence run on the final head of release PR #383**, not on this item PR. |
| R4 | 7 row 18, 7.1, 8.3 | File-only 9.1 guide; listing and file-list tiers unavailable on 9.1; 8.5 keeps all three tiers; per-SKILL.md record (DC4); tested internal dashboard-guide read. |
| R5 | 5, 7, 10, 11 | One atomic PR; listed exclusions are non-goals; a row that cannot reach parity stops the build and is reported, never held silently. |
| A1–A7 | Cited per row as [AC n] | As accepted by R1. Their original architect wording is not reproduced here; this design relies only on the positions R1 accepts. |

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

### 3.2 Endpoint form: unchanged parser plus strict 9.1 grammar [R2, IC]

The classification has two steps. Step 0 is today's parser, unchanged. Step 1
is a pure predicate that only labels an already-accepted value; it never
refuses a value at configuration time. This is what keeps every configuration
accepted today accepted (DC2), while the strict grammar governs only whether a
session may be admitted as 9.1 (DC1).

**Step 0: configuration acceptance (unchanged; derived from current source).**
The option is read and stripped at
[`configuration.py:223-229`][e-cfg223]. It is validated only by
`parse_upstream_dashboard_endpoint` ([`configuration.py:98-167`][e-cfg98]), and
a `ValueError` becomes the single configuration error at
[`application.py:123-128`][e-app123]. Today a value is accepted exactly when:

| # | Condition | Source |
|---|---|---|
| A0 | The stripped value is empty: the endpoint is unconfigured (`None`), not refused. | [`:103-105`][e-cfg103] |
| A1 | `urlsplit` and `.port` raise no `ValueError`. | [`:106-110`][e-cfg103] |
| A2 | Scheme is `http` or `https`, and a hostname is present. | [`:111-114`][e-cfg103] |
| A3 | No fragment. | [`:115-116`][e-cfg103] |
| A4 | Port, if present, is 1–65535. | [`:117-118`][e-cfg103] |
| A5 | A credential is present: username, password, a query, or a path segment that is not `mcp`/`sse` (case-insensitive) and has 8 or more characters. | [`:120-138`][e-cfg120] |

There is no other restriction on path shape, case, encoding, dot segments,
empty segments, trailing slashes or characters (Python `urlsplit` also silently
removes tab, CR and LF). Step 0 stays the **only** configuration-time refusal,
for every version. Its message and the `validate_settings` error text are
unchanged.

**Step 1: `classify_endpoint_form(value) -> "strict_full" | "legacy"`.** It runs
only on a value Step 0 accepted, does no I/O, and is evaluated once per
configuration generation into the immutable endpoint snapshot (section 3.3).
`v` is the stripped value exactly as Step 0 saw it. The value is
`strict_full` only if **every** rule holds, checked in order; the first rule
that fails makes it `legacy`:

| Rule | Requirement on `v` | Rejects (as a 9.1 endpoint) |
|---|---|---|
| S1 | Every character is in `0x21`–`0x7E`, and none is `\`. | whitespace, tab/CR/LF that `urlsplit` would strip, non-ASCII, backslash |
| S2 | `urlunsplit(urlsplit(v)) == v` **and** `str(httpx.URL(v)) == v` (httpx 0.28.1, the transport's own parser). | any form httpx would rewrite: dot segments, upper-case host, default port (`:80` on http, `:443` on https), empty query or other normalization |
| S3 | The path `p = urlsplit(v).path` contains no `%`. | every percent-encoding in the path, including encoded separators and encoded dots (`%2F`, `%2E`) |
| S4 | `p` matches `strict-path` below. | everything else |

```
strict-path = 1*( "/" base-seg ) "/catalog/full"
base-seg    = 1*( ALPHA / DIGIT / "-" / "." / "_" / "~" )   ; ASCII only
              ; and base-seg is not "." and not ".."
              ; and lower(base-seg) is not one of:
              ;   "catalog" "full" "search" "readonly" "default"
```

Notes on the grammar:

- The path ends with exactly `/catalog/full`: lower-case, no trailing slash,
  no `;` parameters, nothing after it.
- There is at least one base segment, and no segment is empty, so `//` is
  rejected anywhere in the path.
- No selector keyword appears anywhere in the base, so a duplicate selector
  (`…/catalog/full/catalog/full`), a stacked mode (`…/readonly/catalog/full`,
  `…/catalog/search/catalog/full`) or `…/default/catalog/full` cannot be
  strict.
- The query and userinfo are not selector inputs. They are allowed and remain
  part of the bound URL. A fragment is already refused by Step 0 (A3).
- Step 0's credential rule still applies. A strict value must carry its
  credential in a base segment of 8 or more characters, in userinfo, or in the
  query.

**Accept/reject examples.** `B` is the synthetic base
`http://ha-mcp.invalid:9583/private_synthetictoken123`. "Today" is Step 0. Each
row was run through the current parser (extracted from `configuration.py` at
the base by AST, not imported) and a reference implementation of Step 1 in an
uncommitted scratch probe. These are the planned committed test vectors
(section 8.2), not executed tests.

| # | Configured value | Today (Step 0) | Form (failing rule) |
|---|---|---|---|
| 1 | `B/catalog/full` | accepted | **strict_full** |
| 2 | `https://ha-mcp.invalid/a/private_synthetictoken123/catalog/full` | accepted | **strict_full** |
| 3 | `B/catalog/full?x=1` | accepted | **strict_full** |
| 4 | `http://u:synthetic-pass@ha-mcp.invalid:9583/base/catalog/full` | accepted | **strict_full** |
| 5 | `B`, `B/`, `B/mcp`, `http://ha-mcp.invalid:9583/synthetic-upstream-secret/mcp/` | accepted | legacy (S4: no terminal `/catalog/full`) |
| 6 | `B/catalog/full/` | accepted | legacy (S4: trailing slash) |
| 7 | `B/catalog//full`, `B//catalog/full` | accepted | legacy (S4: empty segment) |
| 8 | `B/catalog/full/catalog/full`, `B/catalog/search/catalog/full` | accepted | legacy (S4: duplicate selector) |
| 9 | `B/readonly/catalog/full`, `B/default/catalog/full` | accepted | legacy (S4: mode keyword in base) |
| 10 | `B/catalog/search`, `B/catalog/other`, `B/readonly` | accepted | legacy (S4: search, unknown or readonly selector) |
| 11 | `B/Catalog/Full`, `B/catalog/FULL` | accepted | legacy (S4: case) |
| 12 | `B/catalog/full;mode=readonly`, `B;x/catalog/full` | accepted | legacy (S4: `;` not allowed) |
| 13 | `B/catalog%2Ffull`, `B/catalog/%66ull` | accepted | legacy (S3: encoded separator or character) |
| 14 | `http://ha-mcp.invalid:9583/private%2Fsynthetictoken123/catalog/full`, `…/a/%2E%2E/private_synthetictoken123/catalog/full`, `…/synthetic%2Dupstream-secret/mcp` | accepted | legacy (S3: encoding in base) |
| 15 | `B/./catalog/full`, `B/x/../catalog/full`, `http://ha-mcp.invalid:9583/../private_synthetictoken123/catalog/full` | accepted | legacy (S2: httpx removes dot segments) |
| 16 | `http://HA-MCP.invalid:9583/…/catalog/full`, `http://ha-mcp.invalid:80/…/catalog/full` | accepted | legacy (S2: httpx lower-cases host and drops default port) |
| 17 | `B/cata<TAB>log/full`, `B\catalog\full` | accepted | legacy (S1) |
| 18 | `http://ha-mcp.invalid:9583/short/catalog/full` | **refused** (A5: no credential) | n/a: Step 0 refusal, unchanged |
| 19 | `B/catalog/full#frag` | **refused** (A3) | n/a: Step 0 refusal, unchanged |

Rows 5–17 are all accepted today and stay accepted. They include the three
synthetic baseline cases Codex probed: a private path ending in `readonly`
(row 10), a trailing-slash MCP base (row 5), and an encoded private-base
segment (row 14). **Corpus check (scratch, executed).** Of the 269 distinct URL
literals under `tests/`, 134 are accepted by Step 0 and **none** is
`strict_full`. `git grep "catalog/full"` over `hass_mcp_engineering_beta/` and
`tests/` finds only `CHANGELOG.md:14`. So no existing test configuration
changes form.

**Admission by version and form (after initialize, before any list).** The
upstream version is known only from the initialize result, so the version
check happens there. It is one shared function (section 3.3). **Every cell
runs on the same no-redirect transport** [JD-DCL1]; the form never selects a
transport:

| `serverInfo.version` | `strict_full` | `legacy` |
|---|---|---|
| `9.1.0` | Admitted; then protocol, catalog and descriptor checks. | **Refused** before `tools/list`: `upstream_catalog_selector_invalid` if any percent-decoded, lower-cased path segment is `catalog`, `full`, `search`, `readonly` or `default`, otherwise `upstream_catalog_mode_unadmitted` (which names the expected `/catalog/full`). Zero list pages and zero `tools/call`. The value is not echoed. |
| Any other registered version (8.5.0, 8.6.0 and the earlier legacy entries) | Existing contract for that version (adapters, refusals, public results) unchanged. | Existing contract for that version unchanged. |
| Unregistered | Existing refusal. | Existing refusal. |

In every cell, any 3xx on any request is refused with
`upstream_redirect_refused` before a second request is sent (section 3.3).
The contract for non-9.1 versions is never inferred from the path suffix. The
earlier rule that "8.5 + `full` is refused" is withdrawn, because a value of
that shape is accepted today and DC2 forbids refusing it.

**Disclosed residual for Codex closure (not a product decision):**

- **(a) Pre-admission exchange on a `legacy` value.** A `legacy` value answered
  by a 9.1 server still runs these requests before it is refused:
  - `initialize`;
  - `notifications/initialized`, sent inside `ClientSession.initialize()`
    ([SDK `session.py:207`][sdk-init]);
  - possibly the GET stream the SDK starts after initialize
    ([`streamable_http.py:569`][sdk-getstart]);
  - the DELETE cleanup.

  They all run on the no-redirect transport, so no redirect is followed. They
  happen because the version is unknown until initialize returns. Refusing
  earlier would mean refusing values accepted today (DC2). This design reads
  Codex's "zero session initialization for invalid strict endpoints" as zero
  9.1 admission, zero list pages and zero `tools/call` on such a value, which
  section 8.1 tests. If Codex requires zero initialize, DC2 and that
  requirement conflict and need Josh.

The former residual (b) is removed. It described an 8.5 server behind a
strict-form value being refused on a redirect that the legacy transport would
follow. Under [JD-DCL1] every value and every version refuses redirects, so
that is no longer a form-dependent residual; it is the decided behavior.

The classifier's result and the value are never serialized or logged.

### 3.3 One no-redirect transport on every ha-mcp connection [JD-DCL1, IC]

**Decision.** Every SDK session that any Engineering component opens to ha-mcp
uses one shared client factory, `ha_mcp_no_redirect_client_factory(binding)`.
This holds for every upstream version and both endpoint forms. There is no
second transport, no selection by URL shape, and no version check before the
transport is chosen. The endpoint form (section 3.2) decides only 9.1
admission.

`binding` is built per SDK session from the immutable endpoint snapshot
`(url, form, generation)` plus a fresh per-session **refusal latch**. The
factory is passed as `streamablehttp_client(..., httpx_client_factory=…)`
([SDK `streamable_http.py:711-746`][sdk-legacy]) and keeps the SDK factory
signature `(headers, timeout, auth)`.

**Why event hooks rather than a replacement transport.** In httpx 0.28.1:

- request hooks run before the request is handed to the transport, so a hook
  that raises means no byte is sent;
- response hooks run as soon as the transport returns, before any redirect
  handling, and httpx closes the response when a hook raises;
- passing a custom `transport=` also turns off environment proxies.

Hooks therefore add the refusal without changing any other client setting a
consumer has today:

| Consumer | Client settings kept from today | Added by the factory |
|---|---|---|
| Read gateway (`_run_isolated`, `_run_worker`) and every client built on `McpReadGatewayTransport` | the SDK default factory's settings ([`_httpx_utils.py:33-60`][sdk-factory]): `httpx.Timeout(30, read=300)` unless the SDK passes a timeout, the SDK headers, `trust_env` default | request hook, response hook, `follow_redirects=False`, `max_redirects=0` |
| Dashboard transport (`McpDashboardTransport._run`) | the same SDK defaults | the same |
| Dashboard analysis | its existing fence transport `_AnalysisMcpHttp` ([`dashboard_analysis.py:257-380`][e-da257c]), `trust_env=False`, refusal of `auth` ([`:448-452`][e-da448]) | the same hooks and `max_redirects=0`; its own 3xx branch ([`:334-335`][e-da334]) changes its code from `endpoint_rejected` to `upstream_redirect_refused` |

**What the factory adds:**

- **Request hook (before send).**
  - `request.url` must equal the bound `httpx.URL` exactly. A mismatch raises
    `upstream_endpoint_rejected` and nothing is sent.
  - The method must be POST, GET or DELETE. These are the SDK's initialize,
    notification, list, call, GET-stream and session-cleanup methods
    ([SDK `streamable_http.py:604`][sdk-delete] for DELETE).
  - Once the latch is set, every POST and GET is refused before send. Exactly
    one DELETE to the bound URL is still allowed, so the SDK can end the server
    session; a 3xx on that DELETE is refused like any other.
- **Response hook (before the SDK sees the response).** Any status
  `300 ≤ s < 400` sets the latch and raises
  `UpstreamEndpointRefused("upstream_redirect_refused")`. httpx closes the
  response unread. Engineering never reads the body or the `Location` header.
- **Client settings.** `follow_redirects=False` and `max_redirects=0`.

**How the 3xx is refused before any second request.** In `mcp==1.30.0` every
SDK request goes through `stream_within_origin`
([`_httpx_utils.py:112-145`][sdk-stream]):

- POST goes through `request_within_origin`;
- GET goes through `aconnect_sse_within_origin`;
- DELETE is at [`streamable_http.py:604`][sdk-delete].

That loop calls `client.send(..., follow_redirects=False)`. It then follows a
same-origin 3xx itself, building the next request from `Location`, until
`followed == client.max_redirects`. It ignores the client's
`follow_redirects`. Two independent controls stop it, and each has its own
test (section 8.1 item 1):

1. **Guard.** The response hook raises inside `client.send`. The loop never
   receives the 3xx, never computes the next request, and sends nothing to the
   destination.
2. **Budget.** With `max_redirects=0`, the loop's `followed == max_redirects`
   check is true at once. Even if the guard were removed, the first response
   would be yielded and not followed.

The guard is the primary control. Only the guard keeps the SDK's
Location-bearing message ([`_raise_for_unfollowed_redirect`,
`streamable_http.py:79-94`][sdk-unfollowed]) out of reach. The budget only
proves zero requests reach the destination. The SDK, httpx and httpcore
loggers stay silenced as today ([`upstream_read.py:104-107`][e-ur104],
`McpDashboardTransport._silence_url_bearing_library_logs` at
[`mcp.py:234`][e-mcp234]).

**Refusal latch and classification.** If a redirect hits a notification POST
(for example `notifications/initialized`), the SDK's `post_writer` logs the
exception and exits. No later POST is sent and no GET starts. Without a latch,
Engineering's pending operation would then wait until its deadline, and the
refusal would be misreported as `timeout`. So:

- The session runner races the operation against the latch event. When the
  latch is set it cancels the operation at once. The deadline is never the
  classifier.
- Error mapping consults the latch **first**. Both
  `_map_operation_error` ([`upstream_read.py:522-542`][e-ur522]) and
  `_classified_transport_error` ([`mcp.py:539`][e-mcp539]) return the latched
  code before any exception-shape mapping, including the SDK's
  `BaseExceptionGroup` wrapping.
- The new closed codes `upstream_redirect_refused` and
  `upstream_endpoint_rejected` are added to every consumer's closed category
  set and public-code map:
  - `_RECOGNIZED_TYPED_ERROR_CATEGORIES` ([`mcp.py:39`][e-mcp39]) and the
    non-retryable group at [`:118`][e-mcp118];
  - the gateway `_FAILURE_CATEGORIES` ([`upstream_read_gateway.py:243`][e-g243])
    and its provider map at [`:6641`][e-g6641], as `provider_unavailable`,
    not retryable;
  - the dashboard `FAILURE_CATEGORIES` and `ErrorCode` map
    ([`upstream_dashboard.py:126-129`][e-ud126], [`:197`][e-ud197]);
  - the backup and lifecycle maps
    ([`operational_backup.py:530`][e-ob530],
    [`operational_lifecycle.py:1786`][e-ol1786]).
- `upstream_redirect_refused` is a terminal configuration failure. It is
  **not** added to `_STARTUP_ORDERING_FAILURES`
  ([`upstream_read_gateway.py:194`][e-g194]), which holds only
  `endpoint_rejected` with its 600-second startup grace. It is never retried.

**Every Engineering client of ha-mcp (verified at the base).** The SDK is
called at exactly four sites; `git grep "streamablehttp_client("` over the
package finds no other:

| # | SDK session site | Function |
|---|---|---|
| S-a | [`clients/upstream_read.py:231`][e-ur231] | `McpReadGatewayTransport._run_isolated` |
| S-b | [`clients/upstream_read.py:292`][e-ur292] | `McpReadGatewayTransport._run_worker` (retained session) |
| S-c | [`clients/mcp.py:331`][e-mcp331] | `McpDashboardTransport._run` |
| S-d | [`clients/dashboard_analysis.py:455`][e-da455] | dashboard-analysis session (factory at [`:448-452`][e-da448]) |

Every client instance reaches ha-mcp through one of those sites:

| # | Client | Built at | Called from | SDK site |
|---|---|---|---|---|
| C1 | Read gateway | endpoint parsed at [`upstream_read_gateway.py:1126`][e-g1126]; transport built at [`:1197-1198`][e-g1197] | every delegated read, platform and blueprint wrapper and optional YAML read | S-a, or S-b when `retain_session` is set ([`:1201`][e-g1201]) |
| C2 | Fan provider | `FanProvider.configured` ([`upstream_fan.py:39-45`][e-uf39]); timeout `min(ha_timeout, 15)` | [`fan/service.py:918`][e-fs918] | S-a (`retain_session` defaults to false, [`upstream_read.py:93`][e-ur93]) |
| C3 | Power provider | `PowerProvider(FanProvider)` ([`upstream_power.py:6`][e-up6]) | [`power/service.py:55`][e-ps55] | S-a |
| C4 | Helper provider | `HelperProvider(FanProvider)` ([`upstream_helper_value.py:12`][e-uh12]) | [`helper_value/service.py:235`][e-hs235] | S-a |
| C5 | Dashboard provider | endpoint parsed at [`upstream_dashboard.py:380`][e-ud380]; transport built at [`:397-398`][e-ud397] | governed dashboard list, get, plan, write, verification, BPS and the internal guide read | S-c; its analysis read reaches S-d through [`mcp.py:282`][e-mcp282] |
| C6 | Operational backup provider | endpoint parsed at [`operational_backup.py:148`][e-ob148]; transport built at [`:163-164`][e-ob163] | backup provider calls | S-a |
| C7 | Operational lifecycle provider | endpoint parsed at [`operational_lifecycle.py:269`][e-ol269]; transport built at [`:282-283`][e-ol282] | lifecycle provider calls | S-a |

Because the factory is installed at the four SDK sites, C1–C7 get it without
per-client wiring. Each client still has its own named test (section 8.1
item 1), so a future client that bypasses the sites is caught by the AST check
in section 8.2.

**Not ha-mcp MCP connections, and out of scope:**

- [`application.py:124,244,708`][e-app] only parse the option; they open no
  connection.
- `ha_mcp_readmission/registry.py:774` and `providers/upstream_registry.py:289`
  fetch from GitHub with `allow_redirects=False` already.
- `DashboardAnalysisClient`'s aiohttp session talks to Home Assistant Core,
  not to ha-mcp.

The transports receive the endpoint snapshot, not a bare `url` string.

**Admission check after initialize.** One shared function,
`require_selector_agreement(release_version, snapshot, binding)`, is called
from every identity point:

- [`_validate_identity`][e-g2773], with the protocol check at
  [`:2824`][e-g2824];
- the dashboard handshake ([`mcp.py:346`][e-mcp346]);
- the analysis handshake ([`dashboard_analysis.py:463`][e-da463]).

It does two things:

- For **every** version, the session's client must carry the no-redirect
  binding for this snapshot generation. If it does not, the session is refused
  with `upstream_endpoint_rejected`. This is a structural backstop; with the
  factory installed at all four sites it never fires in a correct build.
- For `9.1.0` only, the snapshot form must be `strict_full`. Otherwise the
  session is refused with `upstream_catalog_selector_invalid` or
  `upstream_catalog_mode_unadmitted`, as the section 3.2 table defines. Every
  other registered version returns without change, on either form.

In [`mcp.py:345-346`][e-mcp346] the handshake currently lists tools straight
after `session.initialize()`. The check is inserted between the two, so on a
refusal no list page is requested and no tool is listed or dispatched. It runs
again before every tools/call, because a retained session revalidates its
snapshot generation (see the retained-session row below).

**Per MCP request type (every session, every version, every form).** Each row
is one request type. The factory, bound URL and 3xx refusal are the same on
every row; the SDK site shows where `mcp==1.30.0` issues the request.

| Request type | SDK site (1.30.0) | Binding |
|---|---|---|
| POST `initialize` | `_handle_post_request` ([`streamable_http.py:355`][sdk-post]) | No-redirect factory; bound URL; 3xx refused before a second request. Then `require_selector_agreement`, then protocol `2025-03-26`. |
| POST `notifications/initialized` | sent inside `ClientSession.initialize()` ([`session.py:207`][sdk-init]) | Same. A 3xx sets the latch, so `initialize` fails promptly as `upstream_redirect_refused` and no list follows. |
| POST other notifications (`notifications/cancelled` on a cancel or timeout) | `_handle_post_request` | Same. A refusal here never changes an outcome already recorded (section 6.2 keeps uncertainty). |
| POST `tools/list`, first page and every cursor page | `_handle_post_request` | Same. The order fingerprint is computed over the concatenated `pages[*].tools` order ([`upstream_tool_policy.py:73`][e-p73], check [`:988-994`][e-p988]). A 3xx on any page refuses the whole list. |
| POST `tools/call` | `_handle_post_request` | Same. The existing per-call catalog validator and `before_dispatch` stay in place. |
| GET stream | `handle_get_stream` ([`:273`][sdk-get]), started at [`:569`][sdk-getstart] | Same. A 3xx is refused by the hook, so the SDK's URL-bearing warning is never reached; the logger silencing is kept anyway. The analysis session never sends GET today, and its test asserts that. |
| GET resumption and SSE reconnection (`Last-Event-ID`) | `_handle_resumption_request` ([`:326`][sdk-resume]), `_handle_reconnection` ([`:460`][sdk-reconnect]) | Same client and bound URL; a 3xx is refused on every attempt, and no attempt targets another URL. |
| Retained worker / new session (reconnect at the Engineering level) | Engineering `_run_worker` ([`upstream_read.py:292`][e-ur292]) | Each new SDK session builds a new client and latch from the *current* snapshot. A retained worker whose snapshot generation differs from the current configuration is closed before reuse. The existing authority and revocation checks are unchanged. |
| DELETE session cleanup | `terminate_session` ([`:597-604`][sdk-delete]) | Same client and bound URL; allowed once after a latch. A 3xx is refused and logged by bounded reason only. Cleanup failure never changes a read or write outcome already recorded. |

**Operator message (fixed constant, no interpolation) [IC].** Where
`upstream_redirect_refused` is surfaced to an operator (the bounded error of the
failing delegated call, and the provider status in server health, section 4),
the remediation text is exactly:

> The ha-mcp endpoint answered with an HTTP redirect. Engineering does not
> follow redirects on ha-mcp connections. Set the upstream_dashboard_mcp_url
> option to the exact ha-mcp endpoint URL that answers without a redirect,
> then restart Engineering.

It names the option, not its value. It contains no URL, host, path, query,
status code, `Location` or exception text. A likely cause is a trailing-slash
mismatch: Starlette's `redirect_slashes` answers 307 to the other slash form.
The message does not guess the cause. The release notes may explain it.

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
trailing slash, encoding or case change. The result must satisfy the section
3.2 strict grammar. If the existing base path is itself not strict (for example
it contains a percent-encoding, an empty segment, a dot segment, an upper-case
host, an explicit default port or a selector keyword), the operator must first
change the private base so that it is; Engineering never repairs or
canonicalizes the value. The ha-mcp app options must match the Phase 1
captured configuration:

- app mode;
- tool search enabled;
- filesystem tools enabled;
- global readonly **false**;
- component 2.2.1 tools-only for the optional YAML read.

**What Engineering verifies:**

1. At configuration load, the unchanged Step 0 parser accepts or refuses the
   value exactly as today, and Step 1 records its form (`strict_full` or
   `legacy`) in the endpoint snapshot. No new configuration-time refusal.
2. At initialize, over the no-redirect transport that every ha-mcp session
   uses:
   - `serverInfo.name`;
   - `serverInfo.version == "9.1.0"`;
   - protocol `2025-03-26`;
   - selector agreement (9.1.0 requires `strict_full`).
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
| 9.1 app, suffix not yet appended (`legacy` form) | `upstream_catalog_mode_unadmitted` after initialize and before any list, naming the expected `/catalog/full`. Delegated reads, typed controls and dashboard operations are unavailable. Engineering-native capabilities continue. |
| 9.1 with `/readonly`, `/readonly/catalog/full`, `/catalog/search`, `/default/…`, a duplicate selector, an encoded separator, a dot segment, a trailing slash, a case change or an unknown suffix | The configuration is still accepted (Step 0, unchanged), the form is `legacy`, and 9.1 is refused after initialize and before any list with `upstream_catalog_selector_invalid` (or `upstream_catalog_mode_unadmitted` when no selector keyword is present). Zero list pages and zero `tools/call`. |
| 9.1 with `strict_full` but app options differ (for example global readonly gives the 40-tool catalog) | The existing catalog-mismatch refusal. Stock reads are not admitted on an uncaptured catalog. |
| 9.1 with filesystem tools disabled | **UNKNOWN until implemented.** Parity requires this to behave like 8.5 with filesystem disabled: stock reads admitted, optional YAML unavailable. The expected catalog is S, derived by removing the five filesystem-gated names ([`config_settings.py:678-680`][u91-fs], [`tools_yaml_read.py:651`][u91-yfs]). Until an exact-image case executes it, it is labeled S. If the order fingerprint cannot admit it without weakening the optional boundary, this is reported as a parity blocker, not silently held. |
| Upstream (any version, any form) answers any request with 3xx | `upstream_redirect_refused`, raised before a second request; nothing is followed and `Location` is not read. The provider is unavailable, not retried, and server health shows the category with the fixed operator message (section 3.3). |
| Request URL differs from the bound URL (SDK or library drift) | `upstream_endpoint_rejected` before send. |
| 8.5.0 app with `/catalog/full` (rollback without removing the suffix) | The value stays accepted (form `strict_full`). 8.5 serves no selector routes, so it returns 404 at initialize, which is the existing bounded HTTP failure; the delegated surface stays fail-closed until the suffix is removed. If an 8.5.0 server did answer on such a path, the existing 8.5.0 contract applies, on the no-redirect transport. |
| 8.5.0 app on a value accepted today that it answers **without** a redirect | Same configuration result, adapters, refusals and useful results as today. The only transport difference is that a redirect would be refused; with none, there is no observable change. |
| 8.5.0 app on a value accepted today that it answers **with** a redirect (for example a trailing-slash mismatch) | **Behavior change [JD-DCL1].** Today the same-origin redirect is followed. Now the session fails closed with `upstream_redirect_refused`, nothing is sent to the redirect destination, and the operator message names the fix. The configuration is still accepted (DC2); only the connection is refused. |
| Negotiated protocol other than `2025-03-26` | Existing `unsupported_protocol_version`. |

**Post-deploy acceptance of the production 8.5 URL [JD-DCL1].** Agents have no
live access, so Josh or an operator performs this after Engineering
2.4.2-beta.7 is deployed in Stage A. It changes no configuration and needs no
credential beyond the operator's normal client.

1. Call `get_server_health`. Its `upstream_dashboard` and
   `upstream_read_gateway` blocks come from `health_snapshot()` and
   `health_projection()` ([`health.py:159-190`][e-health159];
   [`upstream_dashboard.py:1925-1975`][e-ud1925]). Pass requires that:
   - `upstream_dashboard.upstream_server_version` is `8.5.0`;
   - `upstream_dashboard.reachable` is true;
   - neither block reports the failure category `upstream_redirect_refused`
     or `upstream_endpoint_rejected`. The exact gateway field that carries the
     category is fixed at implementation from `health_projection()` (S).
2. Run one delegated read, for example `ha_get_state` on an entity the
   operator chooses, and one governed dashboard list. Both must return useful
   results.
3. If step 1 or 2 shows `upstream_redirect_refused`, the production URL
   depends on a redirect. Engineering has already failed closed with the fixed
   operator message (section 3.3), and nothing was sent to the redirect
   destination. The fix is to set `upstream_dashboard_mcp_url` to the exact URL
   that answers without a redirect, then restart Engineering. If that cannot be
   done promptly, roll Engineering back to beta.6 (section 5).

This step is the only proof that production needs no redirect. Section 9
item 2 records it as unknown until it is run.

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
| A | Engineering 2.4.2-beta.7 deployed; app still 8.5.0; URL unchanged (any value accepted today, either form) | The configuration is accepted exactly as today, and the 8.5 adapters, refusals and useful results are unchanged. **Redirects are now refused** [JD-DCL1]: if the production URL depends on a redirect, the delegated surface fails closed with `upstream_redirect_refused` and the fixed operator message. The section 4 post-deploy acceptance proves which case applies. The 9.1 entry is dormant. |
| B1 | Josh updates the app to 9.1.0; URL still `legacy` | Delegated surfaces refuse with `upstream_catalog_mode_unadmitted`, naming `/catalog/full`. No partial 9.1 surface is exposed. |
| B2 | Operator appends `/catalog/full`, giving a `strict_full` value | Full parity surface on 9.1, admitted under the strict `/catalog/full` form on the shared no-redirect transport. |
| Rollback | App back to 8.5.0, **and** suffix removed | Returns to the Stage A state. Removing the suffix is required. If the suffix is left, 8.5 returns 404 and the delegated surface stays fail-closed. |
| Engineering rollback | Engineering returned to beta.6 or earlier while the app is 9.1 | Older Engineering has no 9.1 entry and refuses 9.1, which is existing behavior. The operator must roll the app back to 8.5.0 and remove the suffix first. |

The release notes for #383 must state:

- the Stage B suffix and both rollback orders;
- **the redirect behavior change [JD-DCL1].** Engineering no longer follows
  any HTTP redirect from ha-mcp, on 8.5.0 or 9.1.0. A URL that worked only
  through a redirect (for example with or without a trailing slash) now fails
  closed with `upstream_redirect_refused`. The fix is to configure the exact
  endpoint URL. Include the post-deploy acceptance check in section 4;
- the `ha_get_device` projection deltas on 8.5.0 and 9.1.0 (section 6.5).

That text is written when the base version materializes; it is not written
here.

**Durable records across rollback.** Task, intent and audit records carry the
upstream version and adapter identifiers already. A 9.1-written record that is
read after rollback to 8.5 is displayed and reconciled by its recorded version
and is never re-dispatched. The tests include this.

### 5.1 Acceptance matrix (DC3) [IC]

Three configurations are accepted separately. Each needs useful authorized
success as well as refusal; a configuration accepted only on refusals is not
accepted.

| Configuration | Must succeed | Must refuse or stay unavailable | Named tests (section 8) |
|---|---|---|---|
| **8.5-only.** 8.5.0 peer that answers without a redirect; values accepted today, including the section 3.2 rows 5–17, and a strict-form value served by an 8.5 mock peer | Each retained 8.5 surface keeps its current result on **both** accepted URL forms: all 29 ledger rows' 8.5 dispositions (row 12 through the section 6.5 projection), the three 8.5 skill-guide tiers, governed dashboard list, get, plan, write, verification and BPS (8.5 internal guide arguments unchanged), typed power, fan and helper with verified readback. | Every existing 8.5 refusal is unchanged. **Every 3xx on every request type of every client is refused** [JD-DCL1], with zero requests to the destination. No 9.1 table row is consulted for an 8.5 session. | `test_85_config_corpus_still_accepted`, `test_85_no_redirect_useful_both_forms`, `test_85_legacy_value_redirect_refused`, `test_no_redirect_<client>_<request_type>_<status>_85`, `test_85_useful_calls_per_row`, `test_85_skill_guide_three_tiers`, `test_85_dashboard_guide_read_unchanged`, plus the 8.5 mutation set in 8.4 |
| **9.1-only.** 9.1.0 peer at the captured app configuration; `strict_full` value | Every parity row in the 29-row ledger, the governed dashboard surfaces, the internal dashboard-guide read (section 7.1), and typed controls with verified readback, including high-consequence exact owner-authorized operations. | Every redirect on every request type of every client (8.1 item 1); every `legacy` value, refused before list with no redirect followed; file-list and inventory skill-guide tiers report unavailable; all section 8.1 negatives. | `test_91_strict_admission_positive`, `test_91_no_redirect_useful_strict_full`, `test_no_redirect_<client>_<request_type>_<status>_91`, `test_91_legacy_form_refused_before_list`, `test_91_parity_row_<n>`, `test_91_dashboard_guide_internal_read`, plus the 9.1 mutation set in 8.4 |
| **Side by side.** One Engineering build with both registry entries; the configuration generation changes without restart through A → B1 → B2 → rollback → A, and the Engineering-rollback order | Each stage's row in the table above. After every generation change, the retained worker closes, and the next session uses the new snapshot's form, on the same no-redirect transport. A 9.1-written intent, task or audit record is displayed under 8.5 by its recorded version. | No session or dispatch crosses a generation: a stale 9.1 snapshot never dispatches against an 8.5 configuration or the reverse; a 9.1-written record is never re-dispatched under 8.5. | `test_side_by_side_stage_sequence`, `test_generation_change_closes_retained_worker`, `test_rollback_record_not_redispatched` |

**Exact-image acceptance on both architectures.** Acceptance of each
configuration above needs an exact-image lane on **amd64 and arm64**. Each lane writes an
acceptance manifest: image digest, manifest digest, config digest, Engineering
commit, endpoint form, negotiated protocol, the case list, executed count and
every skip with its reason. Arm64 is currently U (section 0). A missing lane, or
a case that is listed but not executed, makes that configuration unaccepted and
is reported as a **completion blocker** under [R5]; it is never downgraded to a
limitation.

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

### 6.5 Radio credential projection contract (DCL2) [JD-DCL2, IC]

**Rule.** No delegated read passes Thread, Matter, Zigbee or Z-Wave dataset or
credential material, on any upstream version. It is enforced by compiled
**allowlist** projections. This contract adds no upstream call, no upstream
tool and no Core authority.

**Why an allowlist, not a denylist.** The 9.1 Matter enrichment copies
upstream sub-objects without a fixed shape (`active_fabrics` is passed as
returned). The existing sanitizer ([`sanitization.py:26-80`][e-san26]) knows
token, password and Matter setup-code keys, but no Thread network-key, PSKc or
dataset-TLV keys. A denylist would have to predict every future key name. An
allowlist passes only named scalar fields, so an unknown key, including a
credential under an unforeseen name, is dropped and disclosed.

**Citations verified against the exact 9.1 source** (`d2e3f4b0`, archive sha256
`382c2e35…08dd`), as Codex cited them:

- the single-device result is built at
  [`tools_registry.py:449-469`][u91-dev449]; enrichment runs at `:463-469`;
- the Matter projection is at
  [`device_enrichment.py:100-117`][u91-den100]. It copies `network_type`,
  `node_type`, `available`, `network_name`, `ip_adresses` (renamed
  `ip_addresses`), `mac_address`, `active_fabrics` and `active_fabric_index`;
- [`radio/thread.py:89-103`][u91-thread89]: `network_status` returns raw
  `otbr/info` as `border_routers`. Its `ActionSpec` ([`:27-30`][u91-thread27])
  says that carries the active dataset TLVs. `list_datasets` is at
  `:101-103`.

**Credential class (never passed, by any surface, on any version).**

| Family | Material |
|---|---|
| Thread | network key; PSKc; PSKd and any joiner or commissioner credential; any active or pending operational dataset, whole, as TLV hex or as a structured object |
| Matter | setup code, QR and manual pairing payload (already sanitized); operational credentials: NOC, ICAC, RCAC, IPK and epoch keys, private keys, attestation material |
| Z-Wave | S0 and S2 network keys |
| Zigbee | network key, link keys, trust-center link key |

**Network-identity parameters: decision [IC].**

| Parameter | Decision | Reasoning |
|---|---|---|
| Extended PAN ID | Passed **only** where an existing allowlist names it: health `thread_network` border-router `extended_pan_id` (released behavior, [`upstream_platform_reads.py:440-443`][e-pr440]). Never as part of a dataset object. | Not a credential. Thread routers send it in clear in 802.15.4 beacons and MLE Discovery Responses, the way Wi-Fi sends an SSID. It grants no admission and no decryption, and Home Assistant's own UI shows it. Removing it would change released 8.5 health output. |
| Channel | Same: health `thread_network` `channel` only. | Observable on air; useful for interference diagnosis; released behavior. |
| Network name | Passed as Matter `node_diagnostics.network_name` (Thread network name or Wi-Fi SSID). | Broadcast like the extended PAN ID; useful for identifying which network a device joined. |
| PAN ID | **Not allowlisted anywhere.** | No current allowlisted field names it and no use case needs it. |
| Mesh-local prefix | **Excluded.** | It exists only inside the dataset. It is not on air without the network key, and no use case needs it. Thread node IPv6 addresses (ML-EID and RLOC) embed it, which is why Matter `ip_addresses` are conditional (below). |

If Josh judges the extended PAN ID, channel or network name to be
credential-class, removing each is a one-line allowlist change. This is
disclosed for review and is not blocking.

**Every delegated read that touches Matter or Thread (exhaustive, by source
search).** In the 9.1 source outside `tools/radio/`, a search for the
`matter/`, `otbr/`, `thread/`, `zha/` and `zwave_js/` WebSocket command
prefixes finds exactly two tools. 8.5 has the same two
([`tools_registry.py:430,469,511`][u85-reg430];
[`tools_system.py:1381,1464,1517`][u85-sys1381]).

| # | Read | Upstream source (9.1) | Engineering treatment today | Change |
|---|---|---|---|---|
| T1 | `ha_get_device` | ZHA metrics, Z-Wave node status and Matter node diagnostics in [`device_enrichment.py:11-133`][u91-den11], called from [`tools_registry.py:463-469`][u91-dev449] | Pass-through plus sanitizer ([`upstream_read_gateway.py:4212-4275`][e-g4212]) | **New compiled allowlist projection** (below), ledger row 12 P → PJ |
| T2 | `ha_get_system_health` sections `zha_network`, `zwave_network`, `thread_network`, `matter_network` | `_fetch_zha_network` ([`tools_system.py:1281`][u91-sys1281]); `zwave_js/network_status` (`:1370`); `_fetch_thread_network` ([`:1408-1455`][u91-sys1408]) reads `otbr/info` but keeps only `extended_address`, `channel`, `extended_pan_id` and `border_agent_id`; `_fetch_matter_network` ([`:1458-1505`][u91-sys1458]) keeps config-entry id, state and title | Already a fixed wrapper with allowlists: sections at [`upstream_platform_reads.py:43-46`][e-pr43] (`diagnostics`, `config_check` and `zha_network_full` excluded); field maps `_ZHA_DEVICE`, `_ZWAVE_CONTROLLER`, `_ZWAVE_NODE`, `_BORDER_ROUTER`, `_MATTER` at [`:425-444`][e-pr425]; projection at [`:514-542`][e-pr514] via `pick` ([`:364-376`][e-pr364]) | None. New canary tests (section 8.3) |

Reads that do **not** touch these commands:

- `ha_get_integration` is a fixed wrapper. It sends `include_options: False`
  ([`upstream_platform_reads.py:288-296`][e-pr288]) and projects entries
  through `_ENTRY` ([`:661-666`][e-pr661]). Config-entry diagnostics, from
  system health `include=diagnostics` or the `ha_get_integration` diagnostics
  parameters, are never requested.
- Generic state, registry, search and template reads issue no Matter or
  Thread WebSocket command. An integration could still put arbitrary
  attributes into an entity's state. Whether any Core integration exposes
  credential material as a state attribute is U against Core source; it is a
  disclosed residual, and the optional sanitizer backstop below covers the
  named keys.
- `read_only.py` in the 9.1 radio package holds only a mode list.

**`ha_get_device` projection (8.5.0 and 9.1.0 alike).** The rule is
version-independent. 8.5 has the identical Matter enrichment
([`tools_registry.py:505-525`][u85-reg505]), so the projection applies to both
versions. The projected object is the only output. Engineering renders the
text channel from it, and the upstream text and `structuredContent` are never
passed through.

- **Single-device top level:** `success`, `device`, `entities`,
  `entity_count`, `queried_by`, `queried_entity_id`, `warnings` (mapped to
  closed codes, below).
- **Device fields:**
  - identity: `device_id`, `name`, `manufacturer`, `model`, `sw_version`,
    `hw_version`, `serial_number`;
  - placement and ownership: `area_id` (effective area), `integration_type`,
    `integration_sources`, `via_device_id`, `config_entries` (main or child
    row, per [`tools_registry.py:170-186`][u91-dev170]);
  - integration hints: `ieee_address`, `friendly_name`, `mqtt_topic_hint`,
    `zha_trigger_hint`, `node_id`;
  - registry: `name_by_user`, `default_name`, `disabled_by`, `labels`;
  - `connections` and `identifiers`: bounded `[type, value]` string pairs;
  - `entities`: rows of `entity_id`, `name` and `platform`;
  - `radio_metrics`: `lqi`, `rssi`;
  - `node_status`: `node_id`, `status`, `is_routing`, `is_secure`,
    `highest_security_class`, `zwave_plus_version`, `is_controller_node`;
  - `node_diagnostics`: `network_type`, `node_type`, `available`,
    `network_name`, `ip_addresses` (conditional), `mac_address`,
    `active_fabric_index`, and `active_fabrics` as a list of
    `{fabric_index, fabric_id, vendor_id, vendor_name, fabric_label}`. The
    vendor name and fabric label are length-capped and sanitized.
- **Thread addresses.** `node_diagnostics.ip_addresses` is passed only when
  `network_type` is exactly `"wifi"` or `"ethernet"`. Otherwise it is withheld
  and disclosed as unavailable with reason `thread_mesh_local_withheld`,
  because Thread addresses embed the mesh-local prefix.
- **Unknown or non-scalar fields** at any level are dropped. Their presence is
  disclosed by a bounded count, never by name or value. The inner shape of
  upstream `MatterFabricData` is U, so any fabric key outside the five listed is
  dropped.
- **List mode** (admitted today: the 8.5.0 policy entry `automatic_read` has no
  argument restriction): `success`, the pagination
  metadata (`total_count`, `offset`, `limit`, `count`, `has_more`,
  `next_offset`), `total_devices`, `devices` (each through the same
  per-device allowlist), `detail_level`, `integration_filter`, `area_filter`,
  `manufacturer_filter`, `filters`, `bridge`, `usage_hint`.
- **Enrichment warnings.** Upstream appends exception text
  ([`device_enrichment.py:47,88,133`][u91-den11]). Each warning is mapped to a
  closed code: `zha_radio_metrics_unavailable`,
  `zwave_node_status_unavailable`, `matter_node_diagnostics_unavailable`, or
  `enrichment_warning_unrecognized`. Upstream text is never passed.
- **Errors.** The existing error map
  ([`upstream_read_gateway.py:324-325`][e-g324], [`:358-361`][e-g358]) is
  kept. Error payloads are reduced to their closed code and never carry
  upstream detail.
- **Useful results kept.** Identity, child-device `config_entries`, effective
  `area_id`, entities, ZHA LQI and RSSI, Z-Wave node status, and Matter
  network type, availability and fabrics all still pass. The Core
  child-device binding (section 5, [`routes.py:205`][e-cr205]) is unchanged.

**Disclosed deltas on 8.5.0 (release notes).** These are 8.5-visible changes,
made deliberately because the rule is version-independent:

- Thread `ip_addresses` are withheld;
- unknown nested keys are dropped;
- enrichment warnings become closed codes.

**`ha_manage_radio` stays unavailable.** Every reviewed policy classifies it
`mixed_or_requires_wrapper`, so it is not exposed (`tools[58]` in the 8.5.0
and 8.6.0 policies, `tools[59]` in `upstream_tool_policy.json`). The 9.1 policy keeps it non-exposed and adds no wrapper.
`network_status`, `list_datasets` and the dataset-add path stay unreachable.

**Optional backstop [IC].** Add a `thread_credential` redaction category to the
sanitizer for the keys `network_key`, `pskc`, `pskd`, `active_dataset_tlvs`,
`pending_dataset_tlvs`, `dataset_tlvs` and `tlv`, with its own test. It is a
second line of defense for the generic-attribute residual above. The
allowlist is the control.

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
| 12 | ha_get_device ([`:774`][l12]) | PJ [JD-DCL2] | Compiled allowlist projection (section 6.5) on **both** 8.5.0 and 9.1.0. No dataset or credential material is ever passed; Thread `ip_addresses` are withheld (`thread_mesh_local_withheld`); unknown keys are dropped and counted; enrichment warnings become closed codes. Identity, child-device `config_entries`, effective area, entities, ZHA metrics, Z-Wave node status and Matter network type, availability and fabrics stay useful. Core child-device binding per section 5. Enrichment edge cases are U until executed. |
| 13 | ha_get_entity ([`:1794`][l13]) | P | `uses_device_name` is additive. Error contract per rule 9. No registry mutation. |
| 14 | ha_get_entity_exposure ([`:206`][l14]) | P | Body is code-identical. No exposure writes. |
| 15 | ha_get_hacs_info ([`:109`][l15]) | **H** | Held on Core 2026.11 with zero probe; Phase 3 (HAMCP-290). Only the seam is named: a future version-keyed read contract. Source and Marketplace captures do not authorize reachability. |
| 16 | ha_get_history ([`:209`][l16]) | PJ | State rows use the 9.1 parser (rule 3). Statistics use the section 6.1 projection. Pinned arguments per rule 3. Absent attributes are unavailable, not `{}`. Warnings are kept. |
| 17 | ha_get_overview ([`:431`][l17]) | PJ | Selected fields are preserved. The always-added `mcp_client` block is dropped by projection, as it describes Engineering's own internal session, not household data. The changed `settings_url` and `tool_discovery` text are not exposed. Completeness is F3. |
| 18 | ha_get_skill_guide ([`:935`][u91-guide]) | PD [R4] | File-only, like the 8.6 adapter. The `{}` (inventory) and `skill`-only (file-list) tiers return a bounded "unavailable on this upstream version" refusal on 9.1 and are never synthesized; there is no static inventory. `skill+file` maps to `file` for the one bundled skill (section 7.1), and other skills are refused. The strict-BPS acknowledgement line is handled as in 8.6. 8.5 keeps all three tiers unchanged. |
| 19 | ha_get_state ([`:712`][l19]) | P | Visible state is useful. A removed attribute mapping is reported withheld or partial (F3). Hidden-ID refusal is kept, and typed state constraints still refuse on incomplete evidence. |
| 20 | ha_get_todo ([`:309`][l20]) | P | Read only. The public scalar `status` is kept. Retrieval through `todo.get_items` keeps the reviewed read semantics. No todo mutation. |
| 21 | ha_get_zone ([`:170`][l21]) | P | `entity_id` and `icon` are additive. Additional legacy YAML or home rows must not drop the 8.5 rows or broaden actions. |
| 22 | ha_list_floors_areas ([`:142`][l22]) | P | Body is code-identical. No registry writes. |
| 23 | ha_list_services ([`:65`][l23]) | P | Only the annotation changed. Typed service discovery stays exact-domain and is validated independently. |
| 24 | ha_search ([`:61`][l24]) | P | Bounded search only. Visibility omissions are disclosed and empty `warnings` normalized. All-legs failure is a failure, not empty. Component branches are S. The closed schema is kept. |
| 25 | ha_get_blueprint ([`:164`][l25]) | FW | Compiled list/get wrapper over the mixed `ha_manage_blueprints` ([`upstream_blueprint.py:52`][e-ub52]). No import, save, delete or substitute. Full-content provenance and partial data are covered. |
| 26 | ha_get_app ([`:3694`][l26]) | FW | Fixed installed-app projection and stats cap ([`upstream_platform_reads.py:269`][e-pr269]). No store, management or source widening. |
| 27 | ha_get_integration ([`:410`][l27]) | FW | Fixed query window, `options=false`, bounded projection. The new diagnostics path, offset, limit and schema options are never sent. |
| 28 | ha_get_system_health ([`:481`][l28]) | FW | Existing fixed sections only. New diagnostics parameters are inaccessible. The radio sections keep their existing allowlists (section 6.5 T2); credential canary tests are added. |
| 29 | ha_config_get_yaml ([`:484`][l29]) | FW (optional) | Section 6.4. |

**Outside the 29.** These keep their own governed bindings, rebound per version
with success and refusal tests:

- the governed dashboard surfaces: list, get, planning, write, verification,
  analysis and BPS (the internal guide read at
  [`mcp.py:303-317`][e-mcp303] and its 8.6 rewrite at
  [`mcp.py:359-371`][e-mcp359]; for 9.1 the same fixed rewrite, section 7.1);
- typed power, fan and helper.

Held backup and lifecycle surfaces, quarantined raw tools, generic
`ha_call_service`, filesystem siblings, developer and issue-report tools all
remain unavailable.

### 7.1 Bundled SKILL.md record and the internal guide read (DC4) [R4]

**Source pin (ADR-021).** Fetched read-only as untrusted data into the
worktree's ignored `.artifacts/`; nothing was executed.

| Item | Value | Class |
|---|---|---|
| ha-mcp 9.1.0 app | `homeassistant-ai/ha-mcp` at `d2e3f4b0647d815fb32d530fed15a6a99b1d4b33`; archive sha256 `382c2e35d4fbe2ca8228a5788000acf2ef2d9cfb236c93fe4f656cfc437b08dd`, equal to the retained Step 1 archive | S |
| Bundled skills location | `src/ha_mcp/resources/skills-vendor`, a git submodule (`.gitmodules` → `https://github.com/homeassistant-ai/skills.git`). The GitHub archive leaves it empty. | S |
| Submodule pin | gitlink (mode `160000`) at that path → `homeassistant-ai/skills` commit `166defa51c606b3c77d45a9b29a459d1f993f357` (committed 2026-10-29T18:51:26Z), read through the GitHub contents and git-trees APIs for `d2e3f4b0` | S |
| Skills tree | `skills/` tree `caf9a94e2667c0ff3c64c56f370891d0337f3ead`. The tarball at `166defa5` was extracted; all 16 blobs match the API tree by `git hash-object`. The tarball bytes themselves are not byte-stable and are not used as identity. | S |
| Bundled skills | exactly one: `skills/home-assistant-best-practices` (`BEST_PRACTICES_SKILL_NAME`, [`skill_loader.py:153`][u91-skname]) | S |
| How 9.1 serves files | `_register_skill_guide_tool` and `_handle_skill_guide_call` ([`server.py:890-1025`][u91-skserve]) list every regular, non-symlink file through `list_skill_files` ([`skill_loader.py:238-260`][u91-sklist]) and `best_practices_skill` ([`:263-288`][u91-skbp]); `file` defaults to `SKILL.md` | S |
| Captured corroboration | Phase 1 [`receipts/more-edges.json`](evidence/hamcp319/phase1/receipts/more-edges.json) (sha256 `e534521376e660842517c548bcb900a7c7f7c12aa4d384fec68e08662c8f01a8`, matching `SHA256SUMS.json`), pointer `/registry_unavailable_guide/structuredContent/content`: the text after its first 374 characters (the rotating acknowledgement-key prefix) equals the pinned `SKILL.md` exactly | C |

**Per-SKILL.md record.** One bundled `SKILL.md`: blob
`cc99e42595a1a82019f925efec0ec6a086f8a324`, sha256 `6bd2d18d…b29b39b186`,
163 lines. **It names every one of its 15 companion files**, each in the
"Reference Files" table (heading at line 143, rows 148–163), and most also in
the body. The skill directory holds only `SKILL.md` and these 15 files in
`references/`. Omissions:
none.

| Companion file (`references/`) | Named at `SKILL.md` lines |
|---|---|
| `appdaemon.md` | 134, 161 |
| `automation-actions.md` | 82, 110, 114, 115, 125, 151 (some links carry `#` anchors) |
| `backups.md` | 136, 137, 163 |
| `blueprint-guide.md` | 135, 162 |
| `dashboard-cards.md` | 158 |
| `dashboard-guide.md` | 157 |
| `device-control.md` | 92, 101, 111, 112, 128, 129, 155 |
| `domain-docs.md` | 159 |
| `examples.yaml` | 125, 160 |
| `helper-selection.md` | 63, 116, 117, 121, 152 |
| `safe-refactoring.md` | 45, 110, 118, 119, 120, 149 |
| `scenes.md` | 130, 156 |
| `template-guidelines.md` | 138, 139, 153 |
| `triggers-and-conditions.md` | 50, 109, 113, 125, 131, 132, 133, 150 |
| `yaml-only-integrations.md` | 79, 124, 154 |

This record is evidence for R4's question only. It does not create an
inventory: the 9.1 file-list and inventory tiers stay unavailable, and nothing
here is served from a static list.

**Internal dashboard-write guide read.** Strict BPS and governed dashboard
writes fetch one fixed guide through `execute_best_practices_read`
([`mcp.py:303-317`][e-mcp303]) with
`{"skill": "home-assistant-best-practices", "file": "references/dashboard-guide.md"}`.
For 8.6.0, [`mcp.py:359-371`][e-mcp359] requires exactly those arguments and
rewrites them to `{"file": "references/dashboard-guide.md"}`. The 9.1.0 row
gets the same compiled rewrite, keyed by `(9.1.0, ha_get_skill_guide)`. Caller
data cannot choose another file or manufacture an acknowledgement. The target
is `references/dashboard-guide.md`, blob
`bafb545627ca4aac44eb439400f30d77f24eacf8`, sha256
`b4d42e9383fe929ecc87fb52150ceb3222173b528b1cf9f8663218c65544d293`. It is S
only: Phase 1 did not capture this file.

Required test (section 8.3): on a 9.1 fixture, the internal read returns
bounded content equal to that pinned blob after the declared acknowledgement
normalization, and strict BPS and a governed dashboard write proceed. The
public `{}` and `skill`-only calls refuse as unavailable. Any other internal
argument set is refused as `prohibited_argument`. On 8.5, the internal read
and all three public tiers are unchanged. The test stays S until the
exact-image lane runs it; it then becomes C.

**Limitation (U).** How a caller learns the skill name is unchanged by this
design. No static inventory and no refusal text naming the skill is added.

## 8. Test plan

All tests are offline and deterministic, with synthetic loopback peers and
`.artifacts/` scratch. Each item states its evidence class. Section 8.1 holds the
negatives Codex required.

### 8.1 Required negatives

1. **Redirect refusal on every ha-mcp connection [R2, JD-DCL1].** Each test
   uses two mock `httpx` peers with **separate counters**: the origin peer
   (bound URL) and a destination peer (every `Location` target). Tool-list and
   tool-call executions at the origin are counted separately from both.

   **Matrix.** The test name is
   `test_no_redirect_<client>_<request_type>_<status>_<peer>`.
   - **Clients**, one family each (section 3.3):
     - C1 gateway, both `_run_isolated` (S-a) and the retained `_run_worker`
       (S-b);
     - C2 fan, C3 power and C4 helper;
     - C5 dashboard `_run` (S-c) and its analysis read (S-d);
     - C6 backup and C7 lifecycle.
   - **Request types**, each injected at that request only:
     - POST `initialize`;
     - `notifications/initialized` and `notifications/cancelled`;
     - the first `tools/list` page and a later cursor page;
     - `tools/call`;
     - the GET stream, GET resumption, and SSE reconnection with
       `Last-Event-ID`;
     - the Engineering-level retained-worker reconnect;
     - the DELETE cleanup.

     For the analysis session (S-d), the test also asserts that no GET is
     ever sent.
   - **Statuses.** 307 and 308 on every cell. 301, 302 and 303 on every GET
     and POST cell.
   - **Peers.** An 8.5.0 peer and a 9.1.0 peer on every cell. The 8.5 peer
     runs on a `legacy` value and on a strict-form value; the 9.1 peer runs
     on a `strict_full` value.
   - **`Location` targets:**
     - three same-origin mode changes (`/default`,
       `/readonly/catalog/full`, `/catalog/search`);
     - a same-path canonicalization (trailing slash added or removed);
     - a cross-origin target.

   **Assertions on every cell:**
   - The result is `upstream_redirect_refused`, raised promptly. It is not
     `timeout` and does not wait for the deadline (the latch, section 3.3).
   - The origin received **exactly one** request for that phase, plus at most
     the one permitted DELETE cleanup.
   - The destination peer received **zero** requests of any method. This
     count is reported separately from the list and call counts.
   - **Zero underlying tool executions** at the origin. For a redirect on
     `tools/call`, the call was never executed. A redirect on `initialize` or
     on `notifications/initialized` gives zero list POSTs.
   - **No-private-URL canaries.** The bound URL carries a synthetic secret
     path, query and userinfo canary, and every `Location` carries its own
     canary. None appears in any result, exception string or `repr`, audit
     record, log line (all loggers captured at DEBUG), server-health block or
     capability text. The operator text equals the fixed constant exactly
     (`test_redirect_refusal_operator_message_fixed`).

   **Guard and budget, tested one by one:**
   - **Guard** (`test_no_redirect_guard_refuses_with_default_budget`). The
     factory's response hook is in place and `max_redirects` is forced to the
     httpx default of 20. A same-origin 307 is still refused, and the
     destination gets zero requests. The mutant that removes the hook fails
     this test.
   - **Budget** (`test_no_redirect_budget_zero_yields_unfollowed`). The hook
     is bypassed, and the SDK's `stream_within_origin` loop runs against a
     same-origin 307. The 3xx is yielded, not followed, and the destination
     gets zero requests. The mutant `max_redirects > 0` is killed here; it is
     not assumed to be killed by the guard test.
   - **Structure** (`test_no_redirect_factory_settings`). For each consumer,
     the factory-built client has `max_redirects == 0`,
     `follow_redirects is False`, both hooks installed, and that consumer's
     existing settings unchanged (section 3.3 table: analysis keeps
     `trust_env is False` and its fence; the gateway and dashboard keep the
     SDK defaults).
   - **Latch** (`test_no_redirect_latch_classifies_promptly`). A 307 on
     `notifications/initialized` makes the SDK `post_writer` exit. The
     operation still ends with `upstream_redirect_refused` well before its
     deadline, with zero list POSTs. After the latch, POST and GET are
     refused before send, and one DELETE to the bound URL is allowed.

   **Positives (useful behavior kept on peers that do not redirect):**
   - `test_85_no_redirect_useful_both_forms`. An 8.5.0 peer that answers
     without a redirect, on a `legacy` value and on a strict-form value,
     returns useful results on every client: a delegated read, a governed
     dashboard list and get, and a typed power, fan or helper operation with
     verified readback.
   - `test_91_no_redirect_useful_strict_full`. The same on a 9.1.0 peer with
     a `strict_full` value.
   - `test_85_legacy_value_redirect_refused`. An 8.5.0 peer on a `legacy`
     value that today's transport would follow through a same-origin 307 is
     now refused, with zero requests to the destination. This test pins the
     [JD-DCL1] behavior change.
2. **`legacy` value on 9.1 [R2, DC2].** For each `legacy` example in section
   3.2 (rows 5–17), a 9.1.0 peer answers `initialize`. Assert the bounded
   refusal code from the version × form table, zero `tools/list` pages, zero
   `tools/call`, zero requests to any redirect destination, and no echo of the
   value. Assert that the configuration itself was still accepted.
3. **`ws_command` forwarding refusal.** On every typed path (power, fan, helper)
   and every gateway path, supply `ws_command`, `result_fields`,
   `result_attribute_keys`, `wait:true`, `return_response:true`, `verbose:true`,
   extra targets and unknown keys.
   - Assert refusal before dispatch, with zero calls at the peer.
   - Assert the exact constructed key/value set on the positive path.
4. **get_dashboard "No config found" fails closed.** Cover `stored_config:false`
   with `config: None`, an empty-dict config, default absent from the list, a
   list collapsed to `[]`, a `None` fetch, a malformed or truncated list,
   recovery mode or missing YAML, an alias mismatch, and stale membership.
   - Assert set, patch and create are refused.
   - Assert no hash is derived from a non-dict config.
   - Assert existence is not decided absent from `[]`.
   - Assert positive governed operations on a readable dashboard still pass.
5. **Cancellation gives `changed_unverified` with no retry.** For each family,
   inject cancellation or timeout before intent, after intent before send,
   during send, after upstream application before reply, and during
   observation. Use real task cancellation, not only a mocked receipt.
   - Assert no redispatch (peer call count ≤ 1).
   - Assert durable intent survives.
   - Assert the status per the section 6.2 matrix, with the helper reporting
     `changed_unverified`.
   - Assert a proven `call_not_sent` stays distinguishable.
   - Assert subsequent status and recovery reconcile.
6. **Non-admin empty `changed_states`.** `result: []` and
   `{changed_states: [], service_response}` go to observation.
   - With matching readback, the result is verified success.
   - With non-matching or absent readback, the result is uncertain.
   - The result is never `call_not_sent` or failure.

### 8.2 Selector and transport

- **Step 0 corpus (DC2).** Every value in the section 3.2 table, and every
  URL literal under `tests/` that the current parser accepts, is still
  accepted with the same `validate_settings` result. Rows 18 and 19 are still
  refused with today's message. The synthetic base never appears in output.
- **Step 1 grammar (DC1).** Each section 3.2 row gets its form and failing
  rule. Extra vectors: each keyword in each case variant as a base segment;
  `%2f` and `%2F`; `.` and `..` as base segments with and without encoding;
  `?` with an empty query; `:443` on https; a userinfo-only credential; a
  query-only credential; non-ASCII and space characters.
- **Selector agreement.** 9.1 + `legacy` is refused before list. 9.1 +
  `strict_full` is admitted (positive). 8.5 + `legacy` and 8.5 + `strict_full`
  are admitted on the same no-redirect transport (positive: a useful 8.5 call
  succeeds on each). A session with no URL binding, on either version, gives
  `upstream_endpoint_rejected` before list
  (`test_selector_agreement_missing_binding_refused`).
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
  package (today exactly S-a…S-d, section 3.3) passes
  `httpx_client_factory=ha_mcp_no_redirect_client_factory(...)`
  **unconditionally**: no branch on version, endpoint form or URL shape selects
  another factory, and no call relies on the SDK default. A new call site
  without the factory fails the check. The behavior tests in section 8.1 item 1
  are the control; this check is supplementary.

### 8.3 Parity and completeness

- **Two-version calls.** For each of the 29 rows, 8.5-shaped public calls run
  against both 8.5 and 9.1 fixtures. Each row asserts its positive and refusal
  dispositions, the absence of fabricated fields, and unavailable disclosure.
- **Skill guide [R4].** The `{}`, `skill` and `skill+file` forms produce the
  [R4] dispositions on 9.1, and 8.5 keeps all three tiers.
- **Internal dashboard-guide read [R4].** The section 7.1 test:
  `test_91_dashboard_guide_internal_read` (content equals the pinned
  `dashboard-guide.md` blob; strict BPS and a governed dashboard write
  proceed), `test_91_dashboard_guide_rejects_other_arguments`, and
  `test_85_dashboard_guide_read_unchanged`.
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
- **Radio credential projection (section 6.5) [JD-DCL2].** Synthetic data
  only; no production data and no captured device payload. Each test runs on
  an 8.5.0 fixture and a 9.1.0 fixture.
  - `test_device_projection_credential_canaries_<version>`. Distinct canaries
    are placed in Thread network-key, PSKc and active-dataset-TLV fields
    (`network_key`, `pskc`, `active_dataset_tlvs`, plus the same values under
    unforeseen key names), and in Matter, Z-Wave and Zigbee credential-class
    fields (section 6.5 table). They are nested at every level the upstream
    could return: top level, `device`, `node_diagnostics`, inside each
    `active_fabrics` entry, inside `radio_metrics` and `node_status`, inside
    `connections` and `identifiers`, and inside list-mode `devices[]`. Each
    canary is placed in **both** the upstream `content` text and
    `structuredContent`, and once each in only one of them.
    - Inputs also cover malformed shapes (a string where an object is
      expected, a list of mixed types, oversize values, duplicate JSON keys,
      text and structured content that disagree), omitted fields, and upstream
      error payloads and enrichment warnings that carry a canary in their
      exception text.
    - Assert no canary appears in any result, rendered text channel, error,
      warning, audit record, log line (all loggers captured at DEBUG) or
      server-health block. Unknown keys are dropped and counted, never named.
  - `test_device_projection_useful_fields_preserved`. With canaries present,
    identity, child-device `config_entries`, effective `area_id`, entities, ZHA
    LQI and RSSI, Z-Wave node status, and Matter network type, availability,
    network name, MAC address and the five fabric fields are returned exactly.
    The Core child-device binding (section 5) still resolves.
  - `test_device_projection_thread_ip_withheld`. `ip_addresses` pass for
    `network_type` `"wifi"` and `"ethernet"`, and are withheld as
    `thread_mesh_local_withheld` for `"thread"`, a missing type and an
    unrecognized type.
  - `test_device_projection_warnings_closed_codes`. Each enrichment warning
    maps to its closed code; an unrecognized warning maps to
    `enrichment_warning_unrecognized`; no upstream text survives.
  - `test_health_thread_network_credential_canaries`. `ha_get_system_health`
    with `thread_network`, `matter_network`, `zha_network` and
    `zwave_network` fixtures carrying the same canaries, including a raw
    `otbr/info` dataset beside the four allowed border-router fields. Only the
    existing allowlisted fields pass; `extended_pan_id` and `channel` still
    pass as released.
  - `test_manage_radio_unreachable_91` (and `_85`). `ha_manage_radio` is not
    listed and not callable, by name or through any provider route. Across
    every test above, the mock upstream records **zero** `ha_manage_radio`
    calls and zero `otbr/`, `thread/` dataset or `network_status` dispatches.
  - If the optional backstop (section 6.5) is built:
    `test_sanitizer_thread_credential_category`.
- **Rollback.** A 9.1-written intent, task or audit record is read under 8.5
  configuration, displayed by its recorded version, and never re-dispatched.

### 8.4 Fixture provenance

- A fixture that cites a real but unrelated evidence hash or JSON pointer fails
  validation.
- S fixtures carry file:line and are counted separately.
- **Mutations.** Each must fail the named test, which must already pass on the
  unmutated build.

  | Mutation | Killed by |
  |---|---|
  | Drop the 9.1.0 registry entry (9.1 global admission) | `test_91_strict_admission_positive` |
  | Drop the 8.5.0 registry entry (8.5 global admission) | `test_85_useful_calls_per_row` |
  | Drop, in turn, each 9.1 typed or provider route row: power, fan, helper, dashboard, platform, blueprint, YAML, history-slices, error adapter, Core child-device binding, skill-guide rewrite | the matching `test_91_parity_row_<n>` or `test_91_<surface>_positive` |
  | Drop, in turn, each retained 8.5 route row: the same list, plus the 8.5 skill-guide three-tier route | the matching `test_85_<surface>_positive` |
  | Map an 8.5 session through a 9.1 table row | the row's `test_85_<surface>_positive` |
  | Remove selector agreement | `test_91_legacy_form_refused_before_list` |
  | Make Step 1 refuse at configuration time | `test_85_config_corpus_still_accepted` |
  | Leave any one client (C1–C7, S-a…S-d in turn) on the SDK default or a per-client factory | that client's `test_no_redirect_<client>_<request_type>_<status>_<peer>` family |
  | Select the factory only for `strict_full`, only for 9.1, or by URL shape | `test_85_legacy_value_redirect_refused` and the `_85` cells of the matrix |
  | Remove the response hook (3xx guard) | `test_no_redirect_guard_refuses_with_default_budget` |
  | Set `max_redirects > 0` | `test_no_redirect_budget_zero_yields_unfollowed` |
  | Set `follow_redirects` true, or change a consumer's `trust_env` | `test_no_redirect_factory_settings` |
  | Do not consult the latch in classification | `test_no_redirect_latch_classifies_promptly` |
  | Remove the URL binding from the request hook | the URL-drift test (section 8.2) |
  | Interpolate the URL or `Location` into the refusal message | the no-private-URL canaries (8.1 item 1) and `test_redirect_refusal_operator_message_fixed` |
  | Replace the `ha_get_device` allowlist with pass-through plus sanitizer | `test_device_projection_credential_canaries_<version>` |
  | Pass `ip_addresses` for every network type | `test_device_projection_thread_ip_withheld` |
  | Pass upstream warning text | `test_device_projection_warnings_closed_codes` |

### 8.5 Validation sequence

1. Focused suites during implementation.
2. Exact-image lanes on **amd64 and arm64** for each configuration in section
   5.1, each writing the acceptance manifest defined there (image, manifest
   and config digests, Engineering commit, endpoint form, protocol, case list,
   executed count, skips). A missing lane or an unexecuted listed case is a
   completion blocker [R5], not a limitation.
3. **[R3]: one final Evidence run** (`-Parallel 4`, pinned `-BaseRef`, every
   `-AuthorizedProtectedPath`) under the host rule, on the **final head of
   release PR #383** (`release/2.5.0` → `main`), after this PR and the other
   queued items are merged into `release/2.5.0`. It is not run on this item
   PR's head.

None of these was run for this design revision.

## 9. UNKNOWNs carried forward

1. Whether HA keeps executing a service call after the MCP client cancels (not
   determinable from source; Phase 1 does not prove it).
2. Whether the production 8.5 `upstream_dashboard_mcp_url` relies on a
   redirect. The production value is not read during development. Resolved by
   the post-deploy acceptance in section 4: either the 8.5 URL connects with no
   redirect, or Engineering fails closed with the fixed operator message
   naming the fix [JD-DCL1].
3. The behavior of the 9.1 app with filesystem tools disabled (S until
   executed).
4. 9.1 device enrichment edge cases and the Core child-device binding (section
   5).
5. Component 2.2.1 capability branches (`template_diagnose`, `helpers_list`,
   `search_unified`, `find_yaml_key` absent), as exercised through 9.1.
6. The exact 9.1 error envelopes (rule 9). They are S until the exact-image lane
   executes them.
7. arm64 and standalone runtime behavior. Only amd64 app evidence is C. Under
   section 5.1, a missing arm64 lane is a completion blocker, not an accepted
   unknown.
8. Whether any Core integration exposes Thread, Matter, Z-Wave or Zigbee
   credential material as a generic entity state attribute (section 6.5
   residual). U against Core source; the optional sanitizer backstop covers
   the named keys.
9. The literal-key differences in `set_dashboard` other than `reason`
   (`content`, `favorite_entities`, `features`), which are not traced.
10. The production operator's current ha-mcp app options.
11. How a caller learns the bundled skill name on 9.1 (section 7.1). Unchanged
    by this design; no static inventory or naming refusal is added [R4].
12. The original architect wording of A1–A7. R1 accepts Codex's positions,
    which this design cites; the original wording is not needed for any
    decision here.

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
- **No redesign.** No general SDK or protocol redesign. One no-redirect
  transport is used on every ha-mcp connection, for 8.5.0 and 9.1.0 alike
  [JD-DCL1]; its only behavior change is that a redirect is refused instead of
  followed. No new configuration-time refusal.
- **No radio surface.** `ha_manage_radio` stays unavailable; no new upstream
  call, upstream tool or Core authority for radio data [JD-DCL2].
- **Out of this item PR.** Version changes, release declarations, publication,
  deployment, and any live Home Assistant access.
- **Other configurations.** Supporting ha-mcp app configurations other than the
  captured one, beyond the filesystem-disabled parity case in section 4.

## 11. Open decisions

- **Josh:** none blocking. These disclosed parity differences are already
  covered by [R4] or [AC 4] (accepted by [R1]):
  - skill-guide listing and file-list tiers unavailable on 9.1;
  - eval-template `request_id` and `evaluation_time` unavailable;
  - groups `all`/`order` unavailable when absent;
  - dashboard absence unavailable when the registry list collapses.

  The release notes must state them, together with the `/catalog/full` suffix,
  the strict form it must satisfy, and both rollback orders.
- **Josh, release notes (#383), decided [JD-DCL1, JD-DCL2]; recorded for
  completeness:**
  - Engineering no longer follows HTTP redirects on any ha-mcp connection,
    8.5.0 included. A configured URL that only worked through a redirect now
    fails closed with the fixed operator message; the fix is to set the exact
    endpoint URL.
  - The `ha_get_device` deltas on 8.5.0 (section 6.5): Thread `ip_addresses`
    withheld, unknown nested keys dropped, warnings as closed codes.
- **Josh, optional and not blocking:** whether the extended PAN ID, channel or
  network name should be treated as credential-class (section 6.5). The
  design passes them, with reasoning; removing each is a one-line allowlist
  change.
- **Codex closure (implementation-design, not product):** residual (a) in
  section 3.2. A `legacy` value answered by 9.1 runs initialize,
  `notifications/initialized`, possibly the GET stream, and DELETE before
  refusal, now on the no-redirect transport, so no redirect is followed.
  Zero list pages and zero `tools/call`. If Codex requires zero initialize for
  such a value, that conflicts with DC2 and goes to Josh. This design does not
  decide it. Former residual (b) no longer exists: every value uses the same
  no-redirect transport [JD-DCL1].

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
[u91-skname]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/utils/skill_loader.py#L153
[u91-sklist]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/utils/skill_loader.py#L238-L260
[u91-skbp]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/utils/skill_loader.py#L263-L288
[u91-skserve]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/server.py#L890-L1025
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
[sdk-delete]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L597-L604
[sdk-get]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L273
[sdk-resume]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L326
[sdk-post]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L355
[sdk-reconnect]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L460
[sdk-getstart]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L569
[sdk-init]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/session.py#L207
[sdk-legacy]: https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/streamable_http.py#L711-L746
[e-cfg98]: ../hass_mcp_engineering_beta/ha_mcp_engineering/configuration.py#L98-L167
[e-cfg103]: ../hass_mcp_engineering_beta/ha_mcp_engineering/configuration.py#L103-L118
[e-cfg120]: ../hass_mcp_engineering_beta/ha_mcp_engineering/configuration.py#L120-L138
[e-cfg223]: ../hass_mcp_engineering_beta/ha_mcp_engineering/configuration.py#L223-L229
[e-app123]: ../hass_mcp_engineering_beta/ha_mcp_engineering/application.py#L123-L128
[e-ur104]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/upstream_read.py#L104-L107
[e-ur231]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/upstream_read.py#L231
[e-ur292]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/upstream_read.py#L292
[e-mcp331]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L331
[e-mcp346]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L345-L346
[e-mcp303]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L303-L317
[e-mcp359]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L359-L371
[e-da257]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/dashboard_analysis.py#L257-L317
[e-da455]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/dashboard_analysis.py#L449-L456
[e-da463]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/dashboard_analysis.py#L463
[e-app]: ../hass_mcp_engineering_beta/ha_mcp_engineering/application.py#L124
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
[e-da448]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/dashboard_analysis.py#L448-L452
[e-da334]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/dashboard_analysis.py#L334-L335
[e-mcp234]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L234
[e-ur522]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/upstream_read.py#L522-L542
[e-mcp539]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L539
[e-mcp39]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L39
[e-mcp118]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L118
[e-g243]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L243
[e-g6641]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L6641
[e-ud126]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_dashboard.py#L126-L129
[e-ud197]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_dashboard.py#L197
[e-ob530]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_backup.py#L530
[e-ol1786]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py#L1786
[e-g194]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L194
[e-g1197]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L1197-L1198
[e-g1201]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L1201
[e-uf39]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_fan.py#L39-L45
[e-ur93]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/upstream_read.py#L93
[e-fs918]: ../hass_mcp_engineering_beta/ha_mcp_engineering/fan/service.py#L918
[e-up6]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_power.py#L6
[e-ps55]: ../hass_mcp_engineering_beta/ha_mcp_engineering/power/service.py#L55
[e-uh12]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_helper_value.py#L12
[e-hs235]: ../hass_mcp_engineering_beta/ha_mcp_engineering/helper_value/service.py#L235
[e-ud397]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_dashboard.py#L397-L398
[e-mcp282]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/mcp.py#L282
[e-ob163]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_backup.py#L163-L164
[e-ol282]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/operational_lifecycle.py#L282-L283
[e-health159]: ../hass_mcp_engineering_beta/ha_mcp_engineering/health.py#L159-L190
[e-ud1925]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_dashboard.py#L1925-L1975
[e-san26]: ../hass_mcp_engineering_beta/ha_mcp_engineering/sanitization.py#L26-L80
[e-pr440]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L440-L443
[e-pr43]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L43-L46
[e-pr425]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L425-L444
[e-pr514]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L514-L542
[e-pr288]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L288-L296
[e-pr661]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_platform_reads.py#L661-L666
[e-g4212]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L4212-L4275
[e-g324]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L324-L325
[e-g358]: ../hass_mcp_engineering_beta/ha_mcp_engineering/providers/upstream_read_gateway.py#L358-L361
[e-cr205]: ../hass_mcp_engineering_beta/ha_mcp_engineering/ha_core_readmission/routes.py#L205
[u91-dev449]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_registry.py#L449-L469
[u91-dev170]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_registry.py#L170-L186
[u91-den100]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/device_enrichment.py#L100-L117
[u91-den11]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/device_enrichment.py#L11-L133
[u91-thread89]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/radio/thread.py#L89-L103
[u91-thread27]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/radio/thread.py#L27-L30
[u91-sys1281]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_system.py#L1281
[u91-sys1408]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_system.py#L1408-L1455
[u91-sys1458]: https://github.com/homeassistant-ai/ha-mcp/blob/d2e3f4b0647d815fb32d530fed15a6a99b1d4b33/src/ha_mcp/tools/tools_system.py#L1458-L1505
[u85-reg430]: https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_registry.py#L430
[u85-sys1381]: https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_system.py#L1381
[u85-reg505]: https://github.com/homeassistant-ai/ha-mcp/blob/311d6dc273fb4e9a5b8cde0de15f69472a64fe44/src/ha_mcp/tools/tools_registry.py#L505-L525
[e-da257c]: ../hass_mcp_engineering_beta/ha_mcp_engineering/clients/dashboard_analysis.py#L257-L380
