# Token-efficient analytical response contract

This contract applies to future v2 analytical tools. It does not alter the existing
32 MCP tool schemas or compatibility responses.

## Defaults

An analytical response must lead with a concise summary and return only relevant or
anomalous findings by default. Findings and evidence are deduplicated, bounded, and
paginated. Responses report truncation and source coverage, and use stable evidence
references so a later call can drill into a specific item.

Default responses must not include complete Home Assistant registries, raw full
configuration, unchanged configuration, or full traces. Evidence detail is returned
only when explicitly requested and remains bounded.

## Detail levels

- `summary`: counts, conclusion, warnings, truncation, and source coverage; no raw items.
- `standard`: bounded findings and summaries with no raw bulk configuration.
- `evidence`: bounded findings plus deduplicated stable evidence references.

## Models

`ha_mcp_engineering.facilitation` provides:

- `BoundedResult` for a summary, page of findings, warnings, evidence, and coverage;
- `PaginationMetadata` for offset, limit, returned count, total, and next offset;
- `EvidenceReference` for stable provider-scoped evidence identifiers and summaries;
- `SourceCoverage` for requested, completed, partial, and unavailable sources;
- `bounded_result` for deterministic deduplication, limits, pagination, and truncation.

Limits are fail-safe: finding and evidence pages are clamped to 1-100 entries. A
truncated response includes a drill-down warning. Providers separately count evidence
truncation without retaining raw payloads.

## Future tool requirements

New analytical tools must support filtering and pagination, state their detail level,
and report incomplete coverage. A trace finding should summarize the relevant path and
reference the trace; it should not embed the entire trace. Repeated references should be
collapsed. If a source fails, the response must preserve the successful bounded evidence
and mark the overall result partial rather than silently omitting the failure.
