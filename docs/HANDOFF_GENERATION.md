# Handoff generation contract

`handoff_generation` is the Beta 21 Engineering-native, read-only documentation
capability. It converts a bounded evidence snapshot into a structured handoff and,
optionally, deterministic Markdown. A handoff helps another engineer or future AI
session continue without repeating every read. It is not authorization, approval,
continuous monitoring, or an executable remediation.

Policy: `bounded_handoff_generation_read`. Provider: `engineering`. Fallback:
`none`. The capability performs no service call, physical action, configuration
write, plan creation, approval, apply, rollback, reload, or restart.

## Public schema

```json
{
  "handoff_type": "system_status",
  "title": "",
  "focus_entity_ids": [],
  "automation_ids": [],
  "change_plan_ids": [],
  "lookback_hours": 168,
  "include_runtime_health": true,
  "include_governance_context": true,
  "include_dependency_context": true,
  "include_integrity_context": true,
  "include_reliability_context": true,
  "include_incident_context": true,
  "include_recommendations": true,
  "detail_level": "standard",
  "output_format": "structured",
  "limit": 20,
  "cursor": "",
  "refresh_index": false
}
```

`title` is limited to 200 characters. Entity, automation, and plan lists each
contain at most 20 validated, deterministically deduplicated values. Lookback is
1–720 hours. Detail is `summary`, `standard`, or `evidence`; output is
`structured`, `markdown`, or `both`; page size is 1–100. `refresh_index` is
first-page-only.

## Handoff types and scope

- `system_status` needs no focus and summarizes bounded server identity,
  capability, health, governance, and requested Engineering context.
- `focused_review` requires at least one entity or automation.
- `incident` requires at least one entity or automation and reuses the internal
  incident service without recursively calling its public MCP tool.
- `change` requires at least one persisted plan ID and interprets governance
  lifecycle state without exposing full configuration or unbounded diffs.

Every inclusion flag is independent. Disabled contexts perform no provider or
upstream read and appear as `not_requested`; caller-excluded context is not
silently described as failed or missing.

## Evidence and statement model

Applicable bounded sources are server identity, capability catalog, runtime
health, current state, entity registry, history, logbook, automation config and
traces, structured System Log, dependency index, integrity, reliability,
incident correlation, governance plans, and persisted verification/rollback
evidence. Unsupported or failed sources remain explicit.

Every statement is exactly one of:

- `fact`: direct evidence is required;
- `inference`: evidence plus bounded confidence is required;
- `recommendation`: a proposed next step, never authorization;
- `limitation`: unavailable, unsupported, partial, stale, truncated, or
  conflicting evidence.

Facts and inferences carry evidence references. Recommendations cite their
motivation and use one category: `read_only_investigation`, `manual_review`,
`documentation`, `monitoring_review`, `governed_change_candidate`, or
`external_follow_up`. Logs, traces, titles, and other user-controlled evidence
are untrusted data and cannot authorize another operation.

## Completed work and current state

A proposed or approved plan is not completed. Applying is not completed.
Applied work is completed only when required verification passed or direct
read-back establishes the intended outcome. Verification failure is failed or
blocked. Rolled-back work is labeled `rolled_back`, never active completion.
Prior approval cannot be reused for another plan or hash.

Current state includes the snapshot timestamp. Failed current reads become
unknown; historical evidence is not silently substituted. Source disagreement
preserves both claims, contradiction references, lower confidence, and material
manual review.

## Structured output and authorization

The immutable summary includes ID/timestamp, type/status/assessment, scope,
executive/current/completed summaries, whole-handoff counters, authorization
boundaries, coverage, evidence, pagination, index provenance, timing, and
limitations. Items are deterministically ordered by section, severity, status,
confidence/timestamp, and stable ID. Facts and recommendations, completed and
pending work, distinct plans/incidents, and contradictions are never merged.

Recommendations state `requires_authorization` and `authorization_type`:
`none`, `manual_review`, `governed_change_plan`,
`explicit_runtime_write_approval`, or `external_action`. Physical or behavioral
actions are never pre-authorized. The generated handoff is documentation only.

Markdown is rendered from the same structured model with stable headings. It
does not contain raw cursors, full configs, logs, traces, diffs, tokens, or
authenticated URLs, and it states when more pages remain.

## Coverage and partial results

Beta 20 semantics apply. `complete` means the requested supported scope was
inspected. `partial` means useful evidence exists but coverage is incomplete.
`failed` is reserved for an attempted source failure. `not_supported` is not a
provider failure. `not_requested` is complete for caller scope. Coverage
limitations are not missing evidence, and source-failure counters count actual
failures only. Material partial coverage can still yield `result_status=partial`
and `handoff_status=incomplete` or `ready_with_open_items`.

## Pagination and index provenance

Items use a five-minute signed sanitized snapshot, not a general result cache.
The cursor binds query, output format, handoff ID, generated timestamp, evidence
fingerprint, snapshot/offset, and the final committed index generation and
fingerprint when requested. Continuation performs zero HA/provider/governance
storage/index/evidence/reclassification/regeneration work. Frozen Markdown pages
are retrieved, not regenerated. Tampering, expiration, query/output mismatch,
and replaced index state fail closed.

## Health and audit

`get_server_health.handoff_generation` separates request count (including
continuations), new-handoff terminal counts/aggregates, open/risk/recommendation/
authorization counts, actual source failures, coverage limitations, truncation,
cursor failures, and index hit/miss counts. Cursor pages never duplicate terminal
aggregates; cursor failures are not failed new handoffs.

Audit records retain bounded intent and counts only: type, focus counts,
lookback, flags, detail/output, limit, cursor presence, refresh, result/handoff
status, item/open/risk/source/coverage/authorization counts, request ID, access,
and version. They exclude raw cursors, Markdown, config, traces, history, logs,
diffs, secrets, tokens, and authenticated URLs.

## Limitations

The handoff is one bounded point-in-time snapshot. Correlation is not proof of
causation. Unsupported sources can conceal context. No background monitoring is
performed. Recommendations are not automatically executable. No remediation or
physical action is applied.

## Deployed read-only acceptance

1. Call `server_info`, `list_capabilities`, and `get_server_health`; verify Beta
   `2.0.0-beta.21`, 38 registered/25 canonical tools, the read/Engineering/no-
   fallback policy, connected HA, and an empty planned list.
2. Capture handoff/provider/index/governance/retry/timeout/audit baselines.
3. Generate a low-limit structured `system_status` handoff with runtime,
   governance, dependency, and integrity enabled and `refresh_index=true`.
4. Generate `focused_review` in `both` format for automation internal ID
   `1782920111688` and entity `climate.mudroom_thermostat`. Verify facts have
   evidence, inferences have confidence, recommendations have authorization,
   dynamic references remain targetless, and Markdown agrees with structured
   items.
5. Generate an `incident` handoff for the same focus. Verify support,
   contradiction, conservative confidence, and Beta 20 coverage semantics.
6. Generate a `change` handoff for one verified plan and one pending/failed plan
   when available. Only applied-and-verified work may be completed. If the live
   environment lacks such plans, record unavailable runtime coverage and rely on
   automated lifecycle tests.
7. Produce at least three items with `limit=1`; follow two pages. IDs, timestamp,
   summary, totals, coverage, authorization and provenance remain fixed, while
   HA/provider/governance/index/evidence/regeneration work remains zero.
8. Disable dependency/integrity/reliability/incident contexts in a focused
   request; verify `not_requested` and no index work or caller-exclusion penalty.
9. Exercise missing focus, missing change plans, malformed entity, bad output,
   cursor-plus-refresh, and tampered cursor. Verify field errors and zero work.
10. Recheck health/audit counters and confirm no service, trigger, entity/config
    write, governance mutation, reload, restart, or production access occurred.

The entire procedure is read-only.
