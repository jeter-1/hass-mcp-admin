# HA MCP Engineering Server Tool Audit

Date: 2026-07-10

## Purpose

This document classifies every tool currently exposed by the custom HA MCP Engineering Server against the project's intended role: engineering analysis, governance, verification, and documentation that complements the standard `ha-mcp` server.

## Phase 3A provider routing overlay

Lifecycle classification and provider routing answer different questions. The existing
`native`, `transitional`, `delegated`, and `deprecated` labels remain unchanged. The
central routing policy maps all 40 beta tools to these execution/evidence routes:

| Route | Existing tools/capabilities |
| --- | --- |
| `engineering_native` | server/capability diagnostics, audit, plan creation/risk, plan reads/list/approval |
| `standard_mcp_preferred` | ordinary execution/reload pending exact upstream coverage |
| `direct_ha_required` | automation config, traces, blueprint source, config check, governed apply/verification/rollback |
| `transitional_direct` | bounded entity search, exact entity/area/service-catalog reads, template/history/logbook/error log, list automations/devices/entity registry/blueprints; legacy upsert is classified here but denied without a read policy |
| `upstream_dashboard` | read-only dashboard inventory and exact dashboard configuration evidence through one fixed upstream tool |
| `prohibited` | ungoverned destructive automation deletion in the target architecture and secret-bearing diagnostics |

Beta 8 preserves every public schema but enforces the routing overlay at runtime.
Delegated calls return a structured provider-unavailable result while Standard MCP is
unavailable. `delete_automation` fails closed as prohibited; `call_service` and
`reload_domain` cannot silently execute through direct HA. Transitional calls use only
the explicit direct-HA allowlist and record their provider.

Beta 9 corrects the remaining capability-truth mismatch. `get_entity`, `list_areas`,
`search_services`, and `list_services` are lifecycle `transitional`, route
`transitional_direct`, and identify `direct_ha_api`. Standard HA MCP's Assist surface
does not provide their exact semantics; `GetLiveContext` is not substituted. The four
policies are read-only and do not authorize service execution, reload, deletion, or any
physical action.

RC2 adds only `search_entities` to that explicit direct-read set after deployed
acceptance exposed an immediate `provider_unavailable` routing failure. Policy
`bounded_entity_state_search` permits one read-only `/states` inventory, validated
query/domain/limit input, deterministic slim results, and explicit truncation. It
does not enable Standard HA MCP transport or any fallback.

RC3A adds `list_dashboards` and `get_dashboard_config` as beta-native read tools
routed only to `upstream_dashboard`. That adapter allowlists
`ha_config_get_dashboard` and validates exact identity/version, the complete
input schema, reviewed safety annotations, and a security-relevant contract
projection before constructing either approved non-screenshot call shape.
Exact configuration evidence verifies the upstream-compatible 16-character
optimistic-lock hash and separately returns a full 64-character Engineering
evidence hash calculated from complete raw JSON before sanitization or response
omission.
No set/delete/backup/service/physical-action or arbitrary upstream tool can
dispatch. The generic Standard HA MCP gateway remains unavailable.

Beta 7 moves `entity_dependency_analysis` from planned to additive `beta_native`,
category `analysis`, risk `read`, routed `engineering_native`. Beta 12 likewise moves
`automation_reliability_analysis` into additive `beta_native`. Beta 15 adds
`change_impact_analysis`, and Beta 17 adds the read-only
`configuration_integrity_analysis`; Beta 19 adds `incident_correlation`, and Beta
21 adds `handoff_generation`. Beta 22 stabilizes that tool and Beta 23 corrects
shared provider failure accounting without changing the catalog or schemas.
Beta 24 makes the compatibility-visible legacy upsert and every missing direct
policy fail closed, while preserving the same catalog. No planned feature capability remains. All existing public
schemas remain unchanged.

Beta 25 keeps all 38 tools and their schemas. RC3A intentionally raises the
registered count to 40 while preserving every prior schema.
`approve_change_plan` remains
registered but requests an external Home Assistant administrator review; it does
not grant authority. Only the separate internal Ingress application can record
authority-version-2 approval. This is a governance semantic correction, not a
new MCP capability or provider route.

Classifications:

- **Keep** — uniquely useful or strategically important to the Engineering server.
- **Redesign** — valuable capability, but the public contract or implementation should change.
- **Delegate** — standard `ha-mcp` should normally provide this capability.
- **Remove** — does not fit the Engineering server's long-term mission and adds avoidable risk or maintenance.

## Summary

| Classification | Count |
|---|---:|
| Keep | 6 |
| Redesign | 10 |
| Delegate | 4 |
| Remove | 3 |
| **Total** | **23** |

The server's strongest current capabilities are automation traces, blueprint inspection, configuration validation, and its own audit trail. The greatest overlap and risk are concentrated in generic service execution, direct automation replacement/deletion, and domain reloads.

## Tool-by-tool classification

| Tool | Class | Current purpose | Main issue | Recommended destination |
|---|---|---|---|---|
| `get_entity` | Delegate | Read one entity's state and attributes. | Standard `ha-mcp` already provides richer structured state retrieval and projections. | Use standard `ha_get_state`; retain only as an internal primitive if an analysis tool needs direct HA access. |
| `search_entities` | Redesign | Search state machine by ID/friendly name/domain. | Reads one state inventory and returns deterministic bounded slim results with explicit truncation; it still lacks pagination, registry metadata, and fuzzy matching. | Replace publicly with analysis-oriented entity discovery or an internal indexed evidence provider. |
| `get_history` | Redesign | Fetch recent recorder history for one entity. | String refusal, no explicit pagination, no end time, no statistics source, and full HA payload shape. | Keep internally for incident analysis; expose through `analyze_incident` or structured evidence retrieval. |
| `get_logbook` | Redesign | Fetch recent logbook events. | No result limit/pagination or compact schema; output may be noisy. | Internal evidence source for incident timelines and behavioral verification. |
| `get_error_log` | Transitional | Return bounded structured HA System Log warning/error entries. | The System Log buffer is deduplicated and is not the complete raw Core journal. | Recursively sanitize the complete upstream result before reduction; keep fail-closed redaction, truncation, source attribution, untrusted marking, and correlation mandatory. |
| `render_template` | Keep | Evaluate Jinja against live HA state. | Useful and materially supports safe analysis, though output/error schema needs improvement. | Retain as a focused evidence/validation tool; add typed result and timeout/error classification. |
| `list_automation_traces` | Keep | List recent automation traces. | Requires internal ID and returns no pagination metadata. | Retain; normalize identifiers, add limits/pagination, and make it a building block for reliability analysis. |
| `get_automation_trace` | Keep | Retrieve a full execution trace. | Raw, potentially large payload with no compact/section controls. | Retain; add section projection and structured summaries while preserving raw escape hatch. |
| `list_automations` | Redesign | List automation states and internal IDs. | State-machine-only view; no config summary, category, labels, mode, blueprint status, or pagination. | Internal catalog feeding reliability/dependency audits; public output should be structured and pageable. |
| `get_automation_config` | Redesign | Read one automation's stored config. | Uses internal ID only and returns raw config without source metadata or hash. | Retain as internal evidence provider; add canonical ID resolution, source, timestamp, and stable config hash. |
| `list_blueprints` | Delegate | List automation/script blueprints. | Standard `ha-mcp` exposes blueprint discovery and details. | Delegate except where local-file provenance is needed for engineering analysis. |
| `get_blueprint` | Keep | Read installed blueprint source from a read-only mount. | Unique direct-source visibility is useful; raw YAML is untrusted and may contain embedded instructions. | Retain with provenance metadata, content hash, size, and explicit untrusted-content marking. |
| `upsert_automation` | Redesign | Compatibility-visible automation replacement schema; runtime refuses it. | Ungoverned full replacement would bypass immutable plan approval, stale-state checks, snapshots, verification, and rollback. | Fails closed with `governance_required`; use governed change plans. |
| `delete_automation` | Remove | Compatibility-visible deletion schema; runtime refuses it. | Boolean confirmation is not plan-bound approval and provides no dependency check, backup, or rollback receipt. | Fails closed; any future deletion requires a separately reviewed governed design. |
| `check_config` | Keep | Run HA configuration validation. | Good read-only/idempotent safety control; response could be normalized. | Retain as a verification primitive and include in change-verification workflows. |
| `call_service` | Remove | Compatibility-visible generic execution schema; runtime refuses it. | Broad physical-action surface; a denylist or caller Boolean is not sufficient authority. | Use standard `ha-mcp` execution tools where supported; no direct fallback. |
| `reload_domain` | Remove | Compatibility-visible reload schema; runtime refuses it. | Infrastructure/behavioral change with no immutable plan, preflight, or verification. | Use an approved external/general HA workflow; no direct fallback. |
| `list_areas` | Delegate | List area registry. | Standard `ha-mcp` provides richer floor/area topology. | Delegate; retain only as an internal dependency-analysis primitive if necessary. |
| `list_devices` | Redesign | Search device registry. | Limited fields, first-match truncation, no pagination, identifiers/connections omitted despite docstring, and no orphan analysis. | Replace with `analyze_devices` / `find_orphaned_devices` backed by a complete pageable registry client. |
| `list_entity_registry` | Redesign | Search entity registry. | Limited metadata, first-match truncation, no labels/categories/config entry/unique ID, and no actual orphan/reference analysis. | Replace with dependency and configuration-debt tools. |
| `search_services` | Delegate | Search HA services and field names. | Standard `ha-mcp` provides structured service discovery and full schemas. | Delegate to `ha_list_services`. |
| `list_services` | Redesign | Return full HA service catalog. | Potentially huge raw response and largely redundant; useful only for policy classification and compatibility testing. | Make internal to policy/schema validation; do not expose broad full-catalog dumps by default. |
| `get_audit_log` | Keep | Read this server's audit log. | Returns raw JSONL, records attempts rather than verified tool outcomes, hardcodes client as Claude, and may include sensitive arguments. | Retain but redesign storage/events to include request IDs, outcome, duration, client identity, redaction, and execution receipts. |

## Keep: required improvements

### `render_template`

- Return a structured success/error envelope.
- Add a bounded timeout.
- Mark rendered content as data, not instructions.
- Include evaluation timestamp and HA source.

### Automation trace tools

- Accept either entity ID or internal automation ID.
- Add pagination and response-size controls.
- Support selected sections such as trigger, conditions, actions, and errors.
- Produce an optional compact engineering summary without discarding raw evidence.

### `get_blueprint`

- Return file path, content hash, size, source type, and content separately.
- Explicitly label blueprint content as untrusted input.
- Detect and report path/source mismatches.

### `check_config`

- Normalize into `valid`, `errors`, `timestamp`, and `duration_ms`.
- Use as an automatic verification step after approved structural changes.

### `get_audit_log`

- Record tool outcome rather than only HTTP status.
- Add correlation/request IDs.
- Replace hardcoded `user: claude` with neutral client metadata.
- Redact secrets and sensitive fields recursively.
- Record risk class, approval/change ID, delegated server/tool, and verification result.

## Redesign themes

### 1. Raw evidence should become internal providers

The following tools are valuable, but primarily as inputs to higher-level engineering analysis:

- `search_entities`
- `get_history`
- `get_logbook`
- `get_error_log`
- `list_automations`
- `get_automation_config`
- `list_devices`
- `list_entity_registry`
- `list_services`

They should move behind reusable HA client and evidence-provider interfaces. Selected raw tools may remain as diagnostic escape hatches, but the primary public surface should answer engineering questions rather than expose API-shaped data dumps.

### 2. Writes should be plan-bound

`upsert_automation` remains registered only for public-schema compatibility and
fails closed. The implemented automation write workflow requires:

1. Inspect current config and dependencies.
2. Create an immutable change plan.
3. Produce a dry-run/diff.
4. Bind approval to the exact plan hash.
5. Revalidate source hashes immediately before execution.
6. Execute through the narrow governed direct configuration provider.
7. Verify stored config, explicit automation identity, and configuration validity.
8. Record rollback information and an execution receipt.

## Remove/delegate sequence

Do not remove tools immediately. Preserve compatibility while transitioning.

### Stage 1: Mark transitional

Update descriptions and capability reporting to identify:

- `call_service`
- `delete_automation`
- `reload_domain`
- direct `upsert_automation`

as transitional and not preferred for new workflows.

### Stage 2: Add safer replacements

Implement engineering-specific analysis first:

1. `analyze_entity_dependencies`
2. `analyze_automation_reliability`
3. `analyze_change_impact`
4. `analyze_incident`

Then implement plan-bound governance.

### Stage 3: Fail closed (implemented)

Broad direct-write and execution tools remain schema-visible but have no
compatibility escape hatch. Missing direct policy means deny.

### Stage 4: Remove in a major version

Remove obsolete tools only with a documented migration path and major-version release.

## Missing high-value tools

### `server_info`

Reports server identity, version, Git commit, build time, schema version, HA connectivity, and enabled capability groups.

### `list_capabilities`

Identifies each capability as native, delegated, transitional, or unavailable, including risk class.

### `analyze_entity_dependencies`

Finds direct and probable consumers across automations, scripts, scenes, helpers, groups, dashboards, and blueprint inputs.

### `automation_reliability_analysis`

Beta 12 implements a deliberately smaller deterministic first slice: disabled status,
missing/unavailable/unknown/registry-disabled dependencies, repeated trace/action
errors, repeated condition stops, explicit concurrency rejections, correlated sanitized
System Log errors, dynamic-reference gaps, missing blueprint evidence, and absent trace
evidence. It does not speculate about trigger events, delays, retries, thresholds,
manual overrides, or physical safety without direct evidence.

### `analyze_change_impact`

Evaluates proposed renames, removals, integration changes, and automation edits before execution.

### `analyze_incident`

Correlates config, traces, history, logbook, logs, availability, and recent changes into a timeline with evidence and uncertainty.

### `find_configuration_debt`

Finds orphaned helpers, dead dashboard references, disabled entities still referenced, duplicate logic, missing descriptions, and fragile templates.

### Governance tools

- `create_change_plan`
- `approve_change`
- `execute_approved_change`
- `verify_change`
- `rollback_change`
- `generate_handoff`
- `get_change_history`

## Recommended first implementation change

Add `server_info` and `list_capabilities` before altering existing tools.

Why first:

- Establishes reliable identity between the two installed MCP servers.
- Makes architectural boundaries machine-readable.
- Allows existing tools to be marked native, delegated, or transitional without breaking compatibility.
- Provides a stable place to publish version, build, schema, HA connection, and security-mode information.
- Creates the basis for regression tests and future server comparison.

This should be implemented as a small, read-only change with no Home Assistant behavioral impact.

## Verification requirements for the first change

- Both tools return structured JSON-compatible objects, not JSON strings.
- Server name and version match packaged add-on metadata.
- Build provenance is present or explicitly `unknown`.
- HA connectivity is tested without changing HA state.
- Capability classifications match this audit.
- Transitional tools are clearly identified.
- Secrets, endpoint credentials, and tokens are absent.
- Unit tests cover response shape and redaction.

## Decision

The existing public surface should be preserved temporarily for compatibility, but future development should prioritize engineering analysis and governance. Direct execution and generic CRUD should migrate to standard `ha-mcp` or an approved, auditable delegation workflow.
