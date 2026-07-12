# ADR-002: Engineering MCP as a facilitator

- Status: Accepted
- Date: 2026-07-12
- Applies to: HA MCP Engineering Server v2 beta

## Context

Home Assistant already has a standard MCP server intended for broad entity access,
service discovery, and ordinary operations. Reimplementing that surface in the
Engineering MCP would increase maintenance, create inconsistent behavior, enlarge
model context, and provide additional ungoverned write paths.

The Engineering MCP exists to facilitate engineering work between an AI client and
Home Assistant: select evidence, correlate bounded sources, analyze dependencies and
reliability, assess impact and risk, govern changes, verify outcomes, support rollback,
and preserve an auditable handoff.

## Decision

The Engineering MCP is the AI-facing engineering facilitator and governance layer.
The standard Home Assistant MCP remains the preferred general-purpose access and
ordinary-operation layer. Engineering capabilities orchestrate and reduce evidence;
they do not expose raw bulk data by default.

Direct Home Assistant API access is an explicit exception. It remains appropriate for
automation configuration endpoints, exact configuration snapshots, automation traces,
blueprint source, configuration validation, transactional read-back verification,
governed rollback, and server health/connectivity. These capabilities need exact
configuration semantics or safety guarantees that ordinary service execution cannot
provide.

The Standard Home Assistant MCP endpoint is verified as stateless Streamable HTTP at
`/api/mcp`; an add-on can reach it through the fixed Supervisor Core API proxy and
authenticate with its Supervisor token. The Assist API exposed there does not provide
exact entity-ID lookup, complete area-registry enumeration, or service-catalog
discovery. `GetLiveContext` is intentionally rejected as an approximation for these
administrative reads. `StandardHaMcpGateway` therefore remains unavailable in Beta 9
and no upstream transport is configured or called. Beginning with Beta 8, every canonical handler whose capability is
provider-routed enters the facilitator dispatcher. Delegated handlers fail closed while
that gateway is unavailable and never reach their legacy direct-HA implementation.
Transitional handlers enter the same dispatcher, use a reviewed tool-specific direct-HA
exception, and report the provider actually used.

## Decision matrix

| Capability type | Preferred provider | Permitted fallback |
| --- | --- | --- |
| Exact entity-ID state | Direct HA REST API | None |
| Complete area registry | Direct HA WebSocket API | None |
| Service discovery/schema | Direct HA REST API, bounded | None |
| Ordinary service execution | Standard HA MCP | None by default |
| Automation config | Direct HA config API | No generic fallback |
| Automation traces | Direct HA trace API | No generic fallback |
| Blueprint source | Direct HA configuration filesystem/API | No generic fallback |
| Config validation | Direct HA API | No generic fallback |
| Governed apply | Direct HA config API | No standard-MCP execution fallback |
| Verification | Direct HA read-back | No unverified fallback |
| Rollback | Direct HA config API | No generic fallback |
| Dependency analysis | Engineering orchestration | Multiple bounded evidence providers |
| Reliability analysis | Engineering orchestration | Multiple bounded evidence providers |

## Beta 9 capability-truth matrix

| Engineering capability | Required semantics | Standard HA MCP coverage | Direct HA coverage | Selected provider | Completeness | Fallback | Security justification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `get_entity` / current entity state | Exact state and attributes by `entity_id` | Unavailable; `GetLiveContext` filters names/domains/areas | Exact REST state endpoint | `direct_ha_api` | Complete | None | Single read-only entity endpoint |
| `list_areas` / area lookup | Complete area registry | Unavailable; exposed context is not the registry | Exact registry WebSocket command | `direct_ha_api` | Complete | None | Read-only registry command |
| `search_services` / service discovery | Bounded catalog search | Unavailable | Complete catalog with enforced result limit | `direct_ha_api` | Complete within requested bound | None | Read-only catalog endpoint |
| `list_services` / service schemas | Bounded catalog schemas | Unavailable | Complete catalog, explicitly truncated at 50 services when necessary | `direct_ha_api` | Complete or explicitly truncated | None | Read-only catalog endpoint and fixed output bound |

The same matrix is returned by `list_capabilities` as `provider_matrix`. Runtime routing
metadata reports lifecycle, route, selected provider, no-fallback state, and the
specific direct-read policy.

## Routing classifications

- `engineering_native`: governance persistence, risk assessment, analysis,
  auditing, and handoff generation.
- `standard_mcp_preferred`: broad entity search and ordinary service execution where an
  exact upstream contract is available.
- `direct_ha_required`: exact automation configuration, traces, blueprint source,
  configuration checks, governed apply, verification, and rollback.
- `transitional_direct`: an existing direct read/write retained for compatibility
  while migration is incomplete.
- `unsupported`: no approved provider or reliable implementation exists.
- `prohibited`: silent ungoverned actions, destructive fallback, and secret-bearing
  diagnostics.

The public lifecycle labels remain `native`, `delegated`, `transitional`, and
`deprecated`. They describe tool maturity and compatibility. The routing policy is a
separate internal decision describing where a capability may obtain evidence or act.
Partial delegation is represented by provider coverage, missing sources, warnings,
and `completeness=partial`; it is never labeled complete merely because one source
succeeded.

Canonical registration wraps the existing handler rather than changing its signature.
The wrapper applies routing before the handler can perform transport I/O and normalizes
the result into the facilitator envelope. Lifecycle classification alone never grants a
provider or a fallback.

## Intentional direct-HA exceptions

The canonical direct-access allowlist is explicit and fail-closed:

- Transitional evidence: `render_template`, `get_history`, `get_logbook`,
  `get_error_log`, `list_automations`, `list_devices`, `list_entity_registry`, and
  `list_blueprints`.
- Exact administrative reads: `get_entity`, `list_areas`, `search_services`, and
  `list_services`. Each has a distinct read-only policy and no fallback.
- Exact engineering configuration: `get_automation_config`,
  `list_automation_traces`, `get_automation_trace`, `get_blueprint`, and
  `check_config`.
- Legacy configuration write retained during governance migration:
  `upsert_automation`.

Beta 10 narrows the `get_error_log` exception to the read-only
`structured_system_log_read` policy. It uses the admin-only `system_log/list`
WebSocket command and does not authorize Supervisor journal access, raw log-file
mounts, frontend scraping, or any log-triggered action. The existing Phase 3C four-read
capability-truth matrix is otherwise unchanged.

Beta 11 makes the System Log trust boundary explicit: the complete recursive upstream
result is sanitized before evidence selection or response reduction. Unknown fields,
serialized structures, tracebacks, and prompt-like text remain untrusted evidence.
Sanitization failure replaces the affected field and never authorizes an unsanitized
fallback. Redaction metadata reports categories and counts only.

Beta 12 adds `automation_reliability_analysis` as an engineering-native orchestrator.
The `single_automation_reliability_read` policy composes one automation's exact config,
state, optional blueprint, bounded traces, deduplicated referenced-entity/registry
evidence, and sanitized correlated System Log evidence. Source records identify
`direct_ha_api`; the top-level orchestrator identifies `engineering`. There is no
fallback or write permission, and Standard HA MCP coverage is not claimed.

`server_info` and `get_server_health` may perform their documented bounded HA
connectivity probes. Governance apply, exact read-back verification, and rollback use
the direct configuration API under their existing approval and audit controls.
`entity_dependency_analysis` uses its explicitly selected direct administrative sources
and reports partial source coverage. These exceptions do not authorize a
failed delegated canonical call to fall back directly.

`delete_automation` is prohibited by policy. `call_service` and `reload_domain` are
delegated and cannot fall back to direct execution while Standard HA MCP delegation is
unavailable.

## Failure and fallback rules

Provider failures remain visible through a bounded failure category, warning, timing,
coverage, and provider identity. A failed standard-MCP write must never fall back to an
ungoverned direct write. A direct read fallback is allowed only where the central policy
explicitly permits it and the evidence request explicitly opts in. Prohibited fallback
attempts are counted. There is no silent fallback to an unsafe operation.

Future analytical tools must state incomplete source coverage and distinguish complete,
partial, unavailable, and failed provider results. The standard-MCP delegation path must
not be described as operational until a real transport, authentication boundary, failure
mapping, and integration tests exist.

Exact or explicitly loss-tolerant semantics are prerequisites for future Standard MCP
delegation. Provider preference never overrides semantic correctness. Approximate tool
name or schema matching is prohibited.

## Token and credit efficiency

Analysis returns a summary before detail, bounded and deduplicated findings, stable
evidence references, pagination, truncation state, and source coverage. Full registries,
configurations, and traces are omitted unless explicitly requested and still bounded.
This reduces repeated model input, encourages drill-down only where useful, and avoids
paying to transmit unchanged evidence on every analytical call.

## Safety and consistency consequences

- Broad service execution is not reimplemented, avoiding a second generic action plane.
- Native configuration changes retain plan approval, exact snapshot, verification,
  rollback, request correlation, and audit guarantees.
- Failures and partial evidence cannot be silently converted into apparent success.
- Provider metadata excludes credentials and authenticated URLs.
- The facilitator has one deterministic capability policy plus a reviewed,
  tool-specific direct-access exception allowlist.

## Consequences and follow-up

This phase adds internal architecture only and does not add or remove MCP tools. The
callable beta count remains 32. Future analysis tools should depend on
`EngineeringEvidenceProvider`, not REST, WebSocket, or MCP transports directly, and
should use the bounded response primitives documented in
[`../TOKEN_EFFICIENCY.md`](../TOKEN_EFFICIENCY.md).

Phase 3B subsequently adds `entity_dependency_analysis` as the first consumer of these
contracts, increasing the beta manifest to 33 tools without changing the delegation or
fallback decision.

Beta 12 adds the second consumer and first reliability workflow, increasing the
manifest to 34 tools. It proves the intended facilitator pattern: select bounded
evidence, preserve partial-source truth, run deterministic rules, and return stable
references instead of raw bulk configuration or traces.
