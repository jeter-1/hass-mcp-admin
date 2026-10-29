# Engineering 2.4.1-beta.46 release notes

Engineering 2.4.1-beta.46 makes `list_capabilities` return usable data for
every documented call within the response cap (HAMCP-247). It follows
2.4.1-beta.45 and includes it. There is no dependency, add-on configuration,
tool, routing, provider, governance, Core command or signed registry change.
The only input change is two optional, additive `list_capabilities` arguments.

Acceptance: [`V2_4_1_BETA46_ACCEPTANCE.md`](V2_4_1_BETA46_ACCEPTANCE.md).

## Why

On 2026-10-29 the owner called `list_capabilities` on beta.44:

- with no filter it succeeded, but `/data` was omitted (96,902 characters
  against the 60,000 limit) and `response_completeness.retrieval` was empty;
- `status=delegated` did the same (91,274 characters);
- `category=foundation` worked.

Diagnosis (recorded in HAMCP-247):

- A filter narrowed only `tools`, `planned` and `dynamic_upstream`. Every
  filtered call still carried the full `beta_native` (~26k), `provider_matrix`
  (~27k) and gateway snapshot (~13k), so only the size of the dynamic
  upstream list decided whether a call fit.
- Over the limit, the shared envelope tries its compact rendering and then
  drops the largest optional subtree. For the catalog that is `/data`, and its
  retrieval only knows plan and task identifiers, so it was empty.

## The change

The tool now bounds its own data before the envelope sees it. The global
60,000-character cap and the envelope's truncation mechanism are unchanged.

- **Unfiltered call: a bounded summary.** Server identity and version
  (`server`), counts, the canonical `tools`, a `tool_index` of every
  registered tool (`tool`, `status`, `category`), `category_values`, the
  gateway's identity, version, status and count fields with
  `detail_total_counts`, and a `sections` map. `beta_native`,
  `dynamic_upstream` and `provider_matrix` are listed there, not inlined.
- **Filters narrow every section they apply to.** `status` and `category`
  narrow `tools`, `planned`, `beta_native` and `dynamic_upstream` by the
  entry's own status and category (the matching itself is unchanged: exact,
  trimmed, case-insensitive; an unknown value is an empty result, not an
  error). `provider_matrix` rows and the gateway's per-tool rows are kept
  when they name a selected tool. The gateway's identity and count fields are
  always returned, with `detail_scope` and the unfiltered
  `detail_total_counts`, so an empty list is never read as "none exist".
  `status=beta_native` is now documented.
- **`detail_section` (new, optional).** One of `tools`, `beta_native`,
  `dynamic_upstream`, `provider_matrix`, `upstream_read_gateway` returns that
  section in full, filters still applying. An unknown section is refused as
  `invalid_request` with `details.reason: "unknown_section"` and the allowed
  values.
- **`sections` map.** Every response says, for each section, whether it is
  `full`, `summary`, `omitted` or `omitted_size_limit`, its item count, and
  the exact arguments that return it (`request`), filters included.
- **Truthful size statement.** Every response carries `size_limit`
  (`budget_chars`, `omitted_sections`, `retrieval`). A summary or filtered
  view that would exceed the budget drops whole secondary sections first
  (provider matrix, gateway detail, then the tool lists) and lists the calls
  that return each. A requested section that cannot fit is returned in pages
  of its actual rows, in catalog order (`included: "paged"`): each page
  states `item_count` and `page` (`offset`, `row_count`, `next_offset`,
  `final`), has at least one row, and all but the last carry one retrieval
  request for the next page with the same `detail_section` and filters plus
  `detail_cursor`. An oversize gateway (`included: "partial"`) keeps its
  identity and count fields, drops its largest per-tool fields first, names
  them in `detail_omitted_fields`, and gives each the request that pages
  that field's own rows (blocked, held, missing, quarantined, optional and
  the nested readmission rows, reason fields included); no other content
  set is substituted.
- **`detail_cursor` (new, optional).** The opaque cursor from such a
  request. It binds the section, gateway field, filters, position and
  catalog fingerprint, and is valid only with `detail_section` and the same
  filters. A cursor without `detail_section` is refused
  (`cursor_requires_detail_section`); a malformed, mismatched or
  out-of-range one as `invalid_cursor`; one from a catalog that has since
  changed as `stale_cursor`. `invalid_cursor` and `stale_cursor` refusals
  include a `restart` request. `cursor_requires_detail_section` does not;
  supply a section to start the crawl.

The budget is the response limit less a 4,000-character envelope reserve,
measured on the compact rendering the envelope serves when the indented one
is over the limit.

### Before and after, live-shaped (offline)

Full tool response length in characters. The fixture is the 105-tool
synthetic live-shaped catalog: the real upstream read gateway driven with
the stock ha-mcp 8.6.0 capture (77 native plus 28 upstream; live had 106).
Every section fits whole at the real cap, so no row below is paged. The beta.45 columns run the beta.45 projection on the same
fixture. "No data" means the envelope dropped `/data` with empty
retrieval.

| Call | beta.45 | beta.46 | beta.45 at +50% | beta.46 at +50% |
|---|---|---|---|---|
| no argument | no data | 29,426 | no data | 38,875 |
| `status=native` | 58,320 | 12,548 | no data | 13,983 |
| `status=transitional` | 59,510 | 16,870 | no data | 17,788 |
| `status=delegated` | no data | 31,543 | no data | 40,424 |
| `status=deprecated` | 58,267 | 12,347 | no data | 12,346 |
| `status=beta_native` | 57,462 | 48,156 | no data | 58,806 (matrix by retrieval) |
| `category=foundation` | 57,674 | 11,548 | no data | 11,905 |
| `category=evidence` | 58,218 | 35,011 | no data | 56,357 |
| other categories | 57–59k | 11–18k | no data | 11–18k |
| `detail_section=tools` | n/a | 8,440 | n/a | 10,791 |
| `detail_section=beta_native` | n/a | 32,765 | n/a | 50,213 |
| `detail_section=dynamic_upstream` | n/a | 19,075 | n/a | 27,469 |
| `detail_section=provider_matrix` | n/a | 31,087 | n/a | 46,332 |
| `detail_section=upstream_read_gateway` | n/a | 17,496 | n/a | 21,514 |

## Compatibility

- **Input (additive).** `list_capabilities` gains the optional strings
  `detail_section` and `detail_cursor` (default `""`). Every existing call
  stays valid. Its description is rewritten for the summary, the filters,
  the section selector and paging.
- **Output.**
  - Filtered calls keep the established catalog keys (`count`, `filters`,
    `tools`, `planned`, `beta_native`, `core_admission`,
    `dynamic_upstream`, `upstream_read_gateway`, `provider_matrix`,
    `registered_count`, `engineering_registered_count`,
    `dynamic_upstream_count`, `status_values`). Their sections are now
    narrowed by the filter, the gateway gains `detail_scope` and
    `detail_total_counts`, and `filters` gains `detail_section`.
  - Every response gains `view`, `server`, `category_values`, `sections`
    and `size_limit`.
  - The unfiltered call is now the summary. A client that read
    `beta_native`, `dynamic_upstream` or `provider_matrix` from it requests
    them with `detail_section`; before this change that call returned no
    data at all on the live system.
- No tool count, other tool schema, routing, provider, policy, Core gate,
  telemetry, signed profile, probe fingerprint, signed registry, workflow,
  Dockerfile, dependency, add-on option or stable-v1 change. The global
  response cap and the envelope's truncation are unchanged.

**Protected runtime files.** The HAMCP-247 task authorized this scoped
change: `capabilities.py` and `tools/compatibility.py` (the
`list_capabilities` implementation), and the version.

## Rollback

Reinstall the 2.4.1-beta.45 artifact. Nothing is stored. Calls that use
`detail_section` or `detail_cursor` are not supported there.
