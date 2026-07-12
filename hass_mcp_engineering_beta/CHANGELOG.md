# Changelog

## 2.0.0-beta.5

- Map missing or invalid change-plan lookups to `change_plan_not_found` while
  reserving storage failures for real I/O, corruption, serialization,
  permission, and atomic-write failures.
- Treat the expected create-automation availability 404 as a successful probe
  branch so client responses, logs, plan events, and tool-call audits agree.
- Reject existing automation IDs as `configuration_conflict` and malformed or
  failed HA probe responses as real upstream failures.
- Replace transport-lifetime request latency with separate MCP operation, tool,
  and Home Assistant latency summaries; open stream lifetime is excluded.
- Preserve all 32 beta tools and all original 25 compatibility schemas.

## 2.0.0-beta.4

- Add approval-based change plans for creating and updating Home Assistant
  automations, with deterministic dry-run diffs and risk classification.
- Add hash-bound approval, stale-state protection, per-target concurrency,
  controlled apply, read-back verification, and separately approved rollback.
- Add atomic beta-only governance persistence, retention, corrupt-record
  quarantine, restart recovery, safe audit events, and bounded health metrics.
- Expose six governance tools for 32 total callable beta MCP tools while
  preserving all 25 production-compatible tool schemas.

## 2.0.0-beta.3

- Add fail-closed beta deployment and metadata validation for Windows development.
- Add a repeatable beta release checklist, optional health check, and cache-delay
  troubleshooting guidance.
- Keep the production v1.1.2 add-on and runtime unchanged.

## 2.0.0-beta.2

- Explicitly register `get_server_health` with the served FastMCP registry and
  verify its `tools/list`/`tools/call` exposure.
- Correlate upstream HA 4xx/5xx failures across structured tool responses,
  logs, and audit records; entity 404s now use `entity_not_found`.
- Add typed success and failure response contracts and a stable error taxonomy.
- Add request correlation, structured logging, bounded audit records, timing,
  and safe runtime metrics.
- Add beta-native `get_server_health` and migrate `server_info`,
  `list_capabilities`, and `get_error_log` to structured responses.

## 2.0.0-beta.1

- Add an isolated, parallel-installable v2 beta add-on.
- Introduce modular application, gateway, client, model, audit, capability, and
  version boundaries.
- Preserve the v1.1.2 25-tool catalog and public argument schemas.
