# HA MCP Engineering Server 2.2.0-beta.7

Version `2.2.0-beta.7` is a narrow acceptance correction on the accepted F2
foundation. It corrects durable configuration-provider response evidence and
the compatibility/health projection of prohibited plans. It does not change
policy classification, approval authority, task ownership, provider routing,
or execution reachability.

## Truthful provider-response evidence

For a newly executed configuration operation, `response_received` means that
the provider received an HTTP response or WebSocket response frame. It is true
for a completed success response, an empty success response, a received HTTP
error response, or a received WebSocket error frame. It remains false for a
connection failure, timeout, reset, or interruption before any response is
known to have arrived.

The durable provider attempt is reserved before dispatch and updated from this
transport evidence before readback. `response_recorded_at` records that bounded
transition. Provider success, write completion, ambiguous outcome, readback,
and desired-state verification remain separate facts. A later readback mismatch
does not erase an already received response, and successful readback does not
manufacture response evidence after an indeterminate transport outcome. No
response body, header, credential, configuration, or administrator identity is
persisted.

Historical schema-v1 tasks remain byte-preserved. Beta 7 performs no startup
backfill or heuristic rewrite of earlier response fields.

## Non-actionable prohibited projection

A validated F2 prohibited plan remains a visible terminal governance record,
but its public compatibility projection is now internally consistent:

- `status` is `prohibited`;
- `approval.state` is `prohibited`;
- `approval_lifecycle` and `approval_bundle_state` are `prohibited`;
- required acknowledgements are empty;
- no challenge exists and approval is not actionable; and
- apply remains disallowed with `prohibited_change`.

Prohibited records are excluded from awaiting/required approval counters,
pending plan-approval and elevated-acknowledgement counters, external approval
counts, Ingress approval queues, and handoff authorization work. They remain in
the prohibited-policy counter. Their authority-v3 persisted enum fields are not
migrated, and authority-v1/v2 historical records retain their prior projection.

## Compatibility and scope

Beta 7 preserves:

- 25 canonical, 23 Engineering-native, and 48 locally registered tools;
- 26 configured exact-admitted delegated reads and 74 configured live tools;
- zero planned tools and zero fallback;
- task schema version 1 and approval authority version 3;
- `ha-mcp` 7.14.2, protocol `2025-03-26`, compatibility entry
  `ha-mcp-v7.14.2-7917b2d3`, and catalog fingerprint
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`;
- stable v1.1.2; and
- the accepted F2 policy mapping, same-administrator elevated approval,
  durable task reservation, approval consumption, semantic verification,
  duplicate-apply/no-redispatch behavior, and physical non-actuation boundary.

It adds no tool, resource type, provider path, approval mode, policy class,
write route, update/recovery execution, fallback, or historical migration.
