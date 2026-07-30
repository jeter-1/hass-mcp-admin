# HA MCP Engineering Server 2.2.0-beta.2

Version `2.2.0-beta.2` is a narrow corrective release for the F1 durable
execution-task foundation. It does not add a tool, provider, permission,
approval path, fallback, or later roadmap feature.

## Recovery evidence

An original provider response and later operation verification are independent
facts. `provider_response_recorded`, `response_received=true`, and
`response_recorded_at` are now produced only when the original provider call
returns to the execution path.

An Engineering self-restart can still complete as `succeeded_verified` from its
persisted single dispatch, changed process identity, and exact readback. If the
provider response was lost during process replacement, the sole provider
attempt remains `response_received=false`. Startup reconciliation does not
rewrite history and cannot redispatch.

## Counter truthfulness

Operation-specific health reconciles retained plan events with authoritative
task events per plan. One successful apply followed by terminal task reuse
reports two eligible apply attempts, one provider dispatch, one verified
success, and one no-blind-redispatch prevention. Re-reading health or
rehydrating tasks does not increment persistent counts.

## Compatibility

- Task schema version: unchanged at 1.
- Engineering tools: 48.
- Reviewed delegated reads: 26.
- Complete catalog: 74.
- Reviewed upstream recommendation: `ha-mcp` 7.14.2, protocol `2025-03-26`.
- Catalog fingerprint:
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
- Compatibility entry: `ha-mcp-v7.14.2-7917b2d3`.
- Stable v1.1.2, public schemas, plan hashes, approval authority, provider
  routing, dashboard boundaries, and zero fallback are unchanged.

This release does not contain C1, E1, K1, F2, MCP-native Tasks, compensation,
locks, generalized verification, or another development lane.
