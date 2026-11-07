# HAMCP-319 Phase 2 design: ha-mcp 9.1.0 admission side by side with 8.5.0

Status: **design delta for Codex focused closure** (corrections DC1–DC4 to the
reviewed head `3c06f7b1`, plus the verbatim R1–R5 record in section 2). This
document changes no runtime code, policy, registry, workflow, version or release
declaration. Implementation follows on the same pull request only after Codex
closes this design.

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
- **[AC n]**: Codex position A1–A7 as accepted by [R1].
- Evidence classes follow Codex: **C** captured, **S** source-derived, **U**
  unknown or unexecuted.

## 1. Disposition table

### 1.1 Codex findings

| Finding | Revised decision | Authority | Exact citations |
|---|---|---|---|
| **F1 (High)** The configured URL was bound, not the effective endpoint. | **Two parts.** (a) The existing configuration parser is unchanged, and a strict canonical-form predicate (section 3.2) classifies an accepted URL as `strict_full` or `legacy` [R2]. (b) A strict transport is installed through `httpx_client_factory` for every SDK session on a `strict_full` endpoint. It applies to every R2 request type: POST initialize, notifications, list pages and tools/call, the GET stream, GET resumption and reconnection, Engineering-level reconnects and the DELETE cleanup (section 3.3). It does three things: refuses any request whose URL is not the bound URL exactly, refuses every 3xx response before the SDK sees it, and sets `max_redirects=0`. **No redirect is followed on the 9.1 endpoint**, including same-origin canonicalization. **Admission rule.** After initialize and before any list, 9.1.0 is admitted only on a `strict_full` endpoint whose session ran on the strict transport. Every other registered version keeps its existing contract on either form (section 3.2, DC2). **8.5 path.** The `legacy` transport stays byte-for-byte unchanged [R2 "8.5 unchanged"]. **Fingerprints.** The order fingerprint comes from `pages[*].tools` order, and per-descriptor, annotation, output-schema and duplicate checks are kept. | [R2]; strict-transport mechanics [IC] | SDK [`_httpx_utils.py:113-145`][sdk-stream] follows same-origin 307/308 itself and ignores `follow_redirects`; [`:33-60`][sdk-factory] sets no `max_redirects` (httpx default 20). The 3xx message in [`streamable_http.py:79-94`][sdk-unfollowed] includes the Location path. Engineering SDK call sites: [`clients/upstream_read.py:231`][e-ur231], [`:292`][e-ur292], [`clients/mcp.py:331`][e-mcp331], [`clients/dashboard_analysis.py:455`][e-da455]. Existing fence: [`dashboard_analysis.py:257-317`][e-da257]. Selector grammar: [`http_transport.py:122-172`][u-sel]. Default can be full: [`tool_catalog.py:56-60`][u-cat]. |
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

**Where this design depends on each ruling:**

| Ruling | Sections | Design consequence |
|---|---|---|
| R1 | 1.1 (F1–F8), 6.3 (A2), 7 (A6 count and 14/24) | All findings and A1–A7 are accepted; no finding is reopened here. |
| R2 | 3.2, 3.3, 4, 8.1 item 1, 8.2, 8.4 | Strict canonical grammar for the 9.1 endpoint only (DC1); every accepted 8.5 value stays accepted (DC2); every redirect refused on every request type of a strict session; per-request-type mock-transport tests. |
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
check happens there. It is one shared function (section 3.3):

| `serverInfo.version` | `strict_full` (strict transport) | `legacy` (existing transport) |
|---|---|---|
| `9.1.0` | Admitted, if the session ran on the strict transport; then protocol, catalog and descriptor checks. | **Refused** before `tools/list`: `upstream_catalog_selector_invalid` if any percent-decoded, lower-cased path segment is `catalog`, `full`, `search`, `readonly` or `default`, otherwise `upstream_catalog_mode_unadmitted` (which names the expected `/catalog/full`). Zero list pages and zero `tools/call`. The value is not echoed. |
| Any other registered version (8.5.0, 8.6.0 and the earlier legacy entries) | Existing contract for that version, unchanged; on the strict transport, any 3xx is refused. | Existing contract for that version, unchanged, on the existing transport byte-for-byte. |
| Unregistered | Existing refusal. | Existing refusal. |

The contract for non-9.1 versions is never inferred from the path suffix. The
earlier rule that "8.5 + `full` is refused" is withdrawn, because a value of
that shape is accepted today and DC2 forbids refusing it.

**Disclosed residuals for Codex closure (not product decisions):**

- **(a) Pre-admission exchange on a `legacy` value.** A `legacy` value answered
  by a 9.1 server runs `initialize`, `notifications/initialized` (sent inside
  `ClientSession.initialize()`, [SDK `session.py:207`][sdk-init]), possibly the
  GET stream the SDK starts after initialize ([`streamable_http.py:569`][sdk-getstart])
  and the DELETE cleanup on the existing transport, because the version is unknown until initialize returns. Refusing
  earlier would mean refusing values accepted today (DC2). This design reads
  Codex's "zero session initialization for invalid strict endpoints" as zero
  9.1 admission, zero list pages and zero `tools/call` on such a value, which
  section 8.1 tests. If Codex requires zero initialize, DC2 and that
  requirement conflict and need Josh.
- **(b) A value accepted today that already has strict form.** It gets the
  strict transport whatever version answers, so an 8.5 server behind it that
  redirects is refused where today a same-origin redirect would be followed.
  This follows from R2, because the version cannot be known before the first
  request. The repository corpus contains no such value; whether production
  contains one is UNKNOWN (section 9).

The classifier's result and the value are never serialized or logged.

### 3.3 Binding to every request [IC]

**Strict transport.** `StrictBoundMcpTransport(httpx.AsyncBaseTransport)`
generalizes the existing dashboard-analysis fence
([`dashboard_analysis.py:257-317`][e-da257]). It is constructed per SDK session
from an immutable endpoint snapshot `(url, form, generation)`, where `form` is
the section 3.2 result, and is installed **only when `form` is `strict_full`**.
A `legacy` snapshot uses the existing transport unchanged. It is installed
through `streamablehttp_client(..., httpx_client_factory=…)`
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

**Belt and braces.** In `mcp==1.30.0` every SDK request goes through
`stream_within_origin` ([`_httpx_utils.py:113-145`][sdk-stream]: POST through
`request_within_origin`, GET through `aconnect_sse_within_origin`, DELETE at
[`streamable_http.py:604`][sdk-delete]). That loop sends with
`follow_redirects=False` and then follows a same-origin 3xx itself, up to
`client.max_redirects` times, ignoring the client's `follow_redirects`. Two
independent controls therefore stop it, and each has its own test (section
8.1 item 1):

- the transport's 3xx refusal, which raises before the SDK loop sees a
  response; and
- `max_redirects=0` on the factory-built client, which makes the loop yield the
  first response without following it even if the 3xx refusal were removed.

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
and the analysis handshake ([`dashboard_analysis.py:463`][e-da463]). It applies the
version × form table in section 3.2:

- `9.1.0` is admitted only when the snapshot form is `strict_full` **and**
  `transport_kind` is the strict transport. Otherwise it refuses with
  `upstream_catalog_selector_invalid` or `upstream_catalog_mode_unadmitted` as
  that table defines.
- Every other registered version returns without change, on either form, so
  its existing contract applies.

In [`mcp.py:345-346`][e-mcp346] the handshake currently lists tools straight
after `session.initialize()`. The check is inserted between the two, so on a
refusal no list page is requested. This runs before list, so no tool is listed or dispatched on a mismatch. It runs
again before every tools/call, because a retained session revalidates its
snapshot generation (see the retained-session row below).

**Per MCP request type (strict session).** Each row is one R2 request type.
The transport, bound URL and 3xx refusal are the same on every row; the SDK
site shows where `mcp==1.30.0` issues the request.

| Request type | SDK site (1.30.0) | Binding on a `strict_full` session |
|---|---|---|
| POST `initialize` | `_handle_post_request` ([`streamable_http.py:355`][sdk-post]) | Strict transport; bound URL; 3xx refused. Then `require_selector_agreement`, then protocol `2025-03-26`. |
| POST `notifications/initialized` | sent inside `ClientSession.initialize()` ([`session.py:207`][sdk-init]) | Same. A 3xx fails `initialize`, so no list follows. |
| POST other notifications (`notifications/cancelled` on a cancel or timeout) | `_handle_post_request` | Same. A refusal here never changes an outcome already recorded (section 6.2 keeps uncertainty). |
| POST `tools/list`, first page and every cursor page | `_handle_post_request` | Same. The order fingerprint is computed over the concatenated `pages[*].tools` order ([`upstream_tool_policy.py:73`][e-p73], check [`:988-994`][e-p988]). A 3xx on any page refuses the whole list. |
| POST `tools/call` | `_handle_post_request` | Same. The existing per-call catalog validator and `before_dispatch` stay in place. |
| GET stream | `handle_get_stream` ([`:273`][sdk-get]), started at [`:569`][sdk-getstart] | Same. A 3xx is refused at transport level, so the SDK's URL-bearing warning is never reached. The existing logger silencing ([`upstream_read.py:104-107`][e-ur104]) is kept anyway. |
| GET resumption and SSE reconnection (`Last-Event-ID`) | `_handle_resumption_request` ([`:326`][sdk-resume]), `_handle_reconnection` ([`:460`][sdk-reconnect]) | Same transport instance and bound URL; a 3xx is refused on every attempt, and no attempt targets another URL. |
| Retained worker / new session (reconnect at the Engineering level) | Engineering `_run_worker` ([`upstream_read.py:292`][e-ur292]) | Each new SDK session builds a new strict transport from the *current* snapshot. A retained worker whose snapshot generation differs from the current configuration is closed before reuse. The existing authority and revocation checks are unchanged. |
| DELETE session cleanup | `terminate_session` ([`:597-604`][sdk-delete]) | Same transport and bound URL. A 3xx is refused and logged by bounded reason only. Cleanup failure never changes a read or write outcome already recorded. |

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
2. At initialize, over the strict transport:
   - `serverInfo.name`;
   - `serverInfo.version == "9.1.0"`;
   - protocol `2025-03-26`;
   - selector agreement (9.1.0 requires `strict_full` and the strict
     transport).
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
| Upstream answers any request with 3xx | `upstream_redirect_refused`; nothing is followed and `Location` is not read. |
| Request URL differs from the bound URL (SDK or library drift) | `upstream_endpoint_rejected` before send. |
| 8.5.0 app with `/catalog/full` (rollback without removing the suffix) | The value stays accepted. The session uses the strict transport (form `strict_full`). 8.5 serves no selector routes, so it returns 404 at initialize, which is the existing bounded HTTP failure; the delegated surface stays fail-closed until the suffix is removed. If an 8.5.0 server did answer on such a path, the existing 8.5.0 contract applies unchanged (section 3.2), with any 3xx refused (residual (b)). |
| 8.5.0 app on any value accepted today, without the suffix (`legacy`) | Identical to today, on the existing transport. |
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
| A | Engineering 2.4.2-beta.7 deployed; app still 8.5.0; URL unchanged (any value accepted today; `legacy` form unless it already has strict form, residual (b)) | Identical to today. The configuration is accepted exactly as today, and the 8.5 transport, adapters and refusals are unchanged. The 9.1 entry is dormant. |
| B1 | Josh updates the app to 9.1.0; URL still `legacy` | Delegated surfaces refuse with `upstream_catalog_mode_unadmitted`, naming `/catalog/full`. No partial 9.1 surface is exposed. |
| B2 | Operator appends `/catalog/full`, giving a `strict_full` value | Full parity surface on 9.1 through the strict transport. |
| Rollback | App back to 8.5.0, **and** suffix removed | Returns to the Stage A state. Removing the suffix is required. If the suffix is left, 8.5 returns 404 and the delegated surface stays fail-closed. |
| Engineering rollback | Engineering returned to beta.6 or earlier while the app is 9.1 | Older Engineering has no 9.1 entry and refuses 9.1, which is existing behavior. The operator must roll the app back to 8.5.0 and remove the suffix first. |

The release notes must state the Stage B suffix and both rollback orders. That
text is written when the base version materializes; it is not written here.

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
| **8.5-only.** 8.5.0 peer; values accepted today (`legacy`), including the section 3.2 rows 5–17 | Each retained 8.5 surface keeps its current result: all 29 ledger rows' 8.5 dispositions, the three 8.5 skill-guide tiers, governed dashboard list, get, plan, write, verification and BPS (8.5 internal guide arguments unchanged), typed power, fan and helper with verified readback. A same-origin redirect on the `legacy` transport behaves exactly as today. | Every existing 8.5 refusal is unchanged. No 9.1 table row is consulted for an 8.5 session. | `test_85_config_corpus_still_accepted`, `test_85_legacy_transport_unchanged`, `test_85_useful_calls_per_row`, `test_85_skill_guide_three_tiers`, `test_85_dashboard_guide_read_unchanged`, plus the 8.5 mutation set in 8.4 |
| **9.1-only.** 9.1.0 peer at the captured app configuration; `strict_full` value | Every parity row in the 29-row ledger, the governed dashboard surfaces, the internal dashboard-guide read (section 7.1), and typed controls with verified readback, including high-consequence exact owner-authorized operations. | Every redirect on every request type (8.1 item 1); every `legacy` value, refused before list; file-list and inventory skill-guide tiers report unavailable; all section 8.1 negatives. | `test_91_strict_admission_positive`, `test_91_redirect_refused_per_request_type`, `test_91_legacy_form_refused_before_list`, `test_91_parity_row_<n>`, `test_91_dashboard_guide_internal_read`, plus the 9.1 mutation set in 8.4 |
| **Side by side.** One Engineering build with both registry entries; the configuration generation changes without restart through A → B1 → B2 → rollback → A, and the Engineering-rollback order | Each stage's row in the table above. After every generation change, the retained worker closes, and the next session uses the new snapshot's form and transport. A 9.1-written intent, task or audit record is displayed under 8.5 by its recorded version. | No session or dispatch crosses a generation: a stale 9.1 snapshot never dispatches against an 8.5 configuration or the reverse; a 9.1-written record is never re-dispatched under 8.5. | `test_side_by_side_stage_sequence`, `test_generation_change_closes_retained_worker`, `test_rollback_record_not_redispatched` |

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
| 28 | ha_get_system_health ([`:481`][l28]) | FW | Existing fixed sections only. New diagnostics parameters are inaccessible. |
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

1. **Redirect, per request type [R2].** A mock `httpx` transport peer serves a
   `strict_full` endpoint. One test per request type in the section 3.3 table:
   POST `initialize`, `notifications/initialized`, `notifications/cancelled`,
   the first `tools/list` page and a later cursor page, `tools/call`, the GET
   stream, GET resumption, SSE reconnection with `Last-Event-ID`, an
   Engineering-level retained-worker reconnect, and the DELETE cleanup. Each
   test injects 301, 302, 303, 307 and 308 at that request only. The `Location`
   targets are three same-origin mode changes (`/default`,
   `/readonly/catalog/full`, `/catalog/search`), a same-path canonicalization
   (trailing slash), and a cross-origin target.
   - Assert `upstream_redirect_refused`.
   - Assert the redirecting mock received **exactly one** request for that
     phase, and the `Location` target received **zero** requests.
   - Assert **zero underlying provider or tool executions** at the peer: the
     refusal happens before any dispatch. For a redirect on `tools/call`, the
     call was never executed.
   - Assert no `Location`, URL, host or path appears in results, exceptions,
     audit, logs or capability text (canary-scanned).

   Two separate invariant tests cover the controls one by one:
   - **3xx guard.** With the guard in place and `max_redirects` set to the
     httpx default, a 3xx is refused. The mutant that removes the guard fails
     this test.
   - **No-follow client.** A structural test asserts that the strict factory
     builds its client with `max_redirects == 0`, `follow_redirects is False`
     and `trust_env is False`. A behavior test then runs the SDK's
     `stream_within_origin` loop with the 3xx guard bypassed and asserts that
     the 3xx is yielded, not followed (zero requests to `Location`). The mutant
     `max_redirects > 0` is killed by these tests; it is not assumed to be
     killed by the 3xx-guard test.
2. **`legacy` value on 9.1 [R2, DC2].** For each `legacy` example in section
   3.2 (rows 5–17), a 9.1.0 peer answers `initialize`. Assert the bounded
   refusal code from the version × form table, zero `tools/list` pages, zero
   `tools/call`, and no echo of the value. Assert that the configuration itself
   was still accepted.
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
- **Selector agreement.** 9.1 + `legacy`, and 9.1 + `strict_full` on a
  non-strict transport, are refused before list. 9.1 + `strict_full` strict is
  admitted (positive). 8.5 + `legacy` and 8.5 + `strict_full` follow the
  unchanged 8.5 contract (positive: a useful 8.5 call succeeds on each).
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
  package passes the shared strict factory when the snapshot form is
  `strict_full`, or is proven to be on the `legacy` path.

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
  | Map an 8.5 session through a 9.1 table row | `test_85_legacy_transport_unchanged` and the row's 8.5 test |
  | Remove selector agreement | `test_91_legacy_form_refused_before_list` |
  | Make Step 1 refuse at configuration time | `test_85_config_corpus_still_accepted` |
  | Remove the 3xx guard | the 3xx-guard invariant test (8.1 item 1) |
  | Set `max_redirects > 0`, or `follow_redirects`/`trust_env` true | the no-follow client structural and behavior tests (8.1 item 1) |

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
2. Whether production 8.5 relies on any same-origin redirect. This is why the
   `legacy` transport is unchanged [R2 "8.5 unchanged"].
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
8. Whether the production `upstream_dashboard_mcp_url` already has strict form
   (residual (b), section 3.2). The repository corpus has no such value; the
   production value is not read during development.
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
- **No redesign.** No general SDK or protocol redesign. The strict transport is
  scoped to `strict_full` endpoints. No change to 8.5 transport behavior on any
  `legacy` value, and no new configuration-time refusal.
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
- **Codex closure (implementation-design, not product):** the two residuals in
  section 3.2.
  - (a) A `legacy` value answered by 9.1 runs initialize,
    `notifications/initialized`, possibly the GET stream, and DELETE on the
    existing transport before refusal. Zero list pages and zero `tools/call`.
    If Codex requires zero initialize for such a value, that conflicts with
    DC2 and goes to Josh.
  - (b) A value accepted today that already has strict form gets the strict
    transport for every version, so a same-origin redirect from an 8.5 server
    on it is refused.

  If Codex judges either residual material, it is a conflict between R2 and
  DC2 for Josh. This design does not decide it.

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
