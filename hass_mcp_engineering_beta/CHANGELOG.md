# Changelog

## 2.0.0-beta.8

- Route canonical delegated, transitional, direct-required, and prohibited tools
  through the facilitator dispatcher while preserving all 33 tool input schemas.
- Fail delegated calls with a structured provider error when the Standard HA MCP
  gateway is unavailable; never silently invoke the legacy direct-HA implementation.
- Normalize routed responses and attribute provider request, success, failure,
  partial-result, and prohibited-fallback metrics.
- Enforce a reviewed tool-specific allowlist for direct Home Assistant exceptions and
  verify that `entity_dependency_analysis` is present and serializable in `tools/list`.

## 2.0.0-beta.7

- Add the read-only `entity_dependency_analysis` tool; the beta manifest now exposes
  33 tools.
- Build a bounded in-memory dependency index from automation configuration, blueprint
  input/source roles, entity state, and entity registry evidence.
- Add exact structured/template extraction, partial source coverage, cautious stale
  assessment, stable cursors, cache/refresh/invalidation, and bounded detail levels.
- Report unsupported source families and standard-MCP delegation honestly as
  unavailable while preserving all prior schemas and production v1.1.2.

## 2.0.0-beta.6

- Establish the Engineering MCP facilitator architecture, deterministic provider
  routing policy, and transport-independent evidence-provider contracts.
- Represent standard Home Assistant MCP delegation honestly as unavailable until a
  supported nested client transport is configured and verified.
- Add bounded, paginated, deduplicated response and evidence models for future
  analytical tools, plus safe provider-routing health counters.
- Replace free-text safety keyword matching with structured action, service, target,
  entity-domain, and blueprint-input risk evidence; harmless descriptive text no
  longer produces high-risk plans.
- Preserve all 32 beta tools, the original 25 schemas, the seven beta-native schemas,
  governance persistence compatibility, and production v1.1.2.

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
