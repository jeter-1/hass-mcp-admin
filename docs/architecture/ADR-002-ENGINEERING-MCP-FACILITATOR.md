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

The current beta has no configured or verified nested standard-MCP client transport.
`StandardHaMcpGateway` therefore reports `provider_unavailable`; it never fabricates a
delegation result. Existing compatibility reads continue through their current direct
HA clients and are classified `transitional_direct` until a supported standard-MCP
transport is introduced and contract-tested.

## Decision matrix

| Capability type | Preferred provider | Permitted fallback |
| --- | --- | --- |
| General entity state | Standard HA MCP | Direct HA only when explicitly justified by policy and requested |
| Service discovery | Standard HA MCP | None by default |
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

## Routing classifications

- `engineering_native`: governance persistence, risk assessment, analysis,
  auditing, and handoff generation.
- `standard_mcp_preferred`: current state, broad search, areas, and ordinary service
  discovery/execution.
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
- The facilitator has one deterministic routing policy instead of tool-specific fallback
  decisions.

## Consequences and follow-up

This phase adds internal architecture only and does not add or remove MCP tools. The
callable beta count remains 32. Future analysis tools should depend on
`EngineeringEvidenceProvider`, not REST, WebSocket, or MCP transports directly, and
should use the bounded response primitives documented in
[`../TOKEN_EFFICIENCY.md`](../TOKEN_EFFICIENCY.md).

Phase 3B subsequently adds `entity_dependency_analysis` as the first consumer of these
contracts, increasing the beta manifest to 33 tools without changing the delegation or
fallback decision.
