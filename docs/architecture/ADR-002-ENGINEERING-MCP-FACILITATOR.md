# ADR-002: Engineering MCP as a facilitator

## RC3A dashboard evidence decision

RC3A introduces a provider-specific exception to the still-unavailable generic
Standard HA MCP path. `upstream_dashboard` is not a general delegated gateway:
it validates one reviewed upstream tool contract and offers only typed,
argument-constrained dashboard inventory and exact configuration evidence
operations. The upstream 7.13.0 tool is mixed-operation, but Engineering cannot
construct its screenshot/write form. Its route is
`upstream_dashboard`, its fallback is none, and its write allowlist is empty.

This preserves the facilitator decision: the Engineering server may acquire
bounded, exact dashboard evidence needed for later engineering analysis, while
dashboard mutation, service execution, physical action, and arbitrary upstream
tool forwarding remain outside the boundary.

## Beta 25 authority clarification

Facilitation and authenticated MCP access do not imply human change authority.
The MCP channel may create an immutable plan and request review, while exact-hash
approval is granted only through a separate Home Assistant admin Ingress
application. Apply and rollback have distinct grants. This preserves the
facilitator role while retaining direct Home Assistant configuration writes only
behind governance, external authority, verification and rollback controls.

## Beta 23 observability clarification

A routing decision may select `engineering`, `direct_ha_api`, or a future
`standard_ha_mcp` provider before any operation is attempted. Selection alone is
not dispatch. Shared provider metrics require explicit dispatch provenance;
pre-provider validation/authentication/rate-limit/policy errors and local snapshot
reads remain application events. This prevents declared capability metadata from
being mistaken for attempted provider work while retaining actual failure and
timeout attribution.

## Beta 22 evidence-backed handoffs

The facilitator may reduce a bounded investigation into a point-in-time handoff.
`handoff_generation` classifies read evidence, governance lifecycle state,
conflicts, recommendations, and authorization boundaries. It does not duplicate
ordinary HA operations, call public MCP tools recursively, monitor continuously,
or turn a recommendation into authority. Structured output is canonical;
Markdown is a deterministic projection of the same model.

Multiple internal contexts may reuse one dependency snapshot. The facilitator
therefore normalizes coverage by logical source and provider capability before
status, audit, and health calculations. Reuse cannot fabricate a second failure.
Retained governance history is evidence, not active authorization or pending work.

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
| Bounded entity state search | Direct HA REST API | None |
| Exact entity-ID state | Direct HA REST API | None |
| Complete area registry | Direct HA WebSocket API | None |
| Service discovery/schema | Direct HA REST API, bounded | None |
| Dashboard inventory/config evidence | Version-pinned, reviewed argument-constrained `upstream_dashboard` adapter | None |
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
| `search_entities` / broad entity search | Bounded state-machine search by entity ID/friendly name with optional exact domain filter | Unavailable | One complete state inventory with bounded slim output | `direct_ha_api` | Complete within requested bound or explicitly truncated | None | Single read-only inventory; validated input; no attributes or writes |
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
- `standard_mcp_preferred`: ordinary service execution where an exact upstream
  contract is available.
- `direct_ha_required`: exact automation configuration, traces, blueprint source,
  configuration checks, governed apply, verification, and rollback.
- `transitional_direct`: an existing direct read/write retained for compatibility
  while migration is incomplete, including the bounded entity-state search.
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

The canonical direct-access allowlist is explicit and fail-closed. Beta 24
requires both allowlist membership and a matching tool-specific read policy;
missing policy or access-type mismatch means deny:

- Transitional evidence: `render_template`, `get_history`, `get_logbook`,
  `get_error_log`, `list_automations`, `list_devices`, `list_entity_registry`, and
  `list_blueprints`.
- Exact administrative reads: `search_entities`, `get_entity`, `list_areas`,
  `search_services`, and `list_services`. Each has a distinct read-only policy
  and no fallback. Entity search uses `bounded_entity_state_search`, one
  `GET /states` inventory, deterministic ordering, and explicit truncation.
- Exact engineering configuration: `get_automation_config`,
  `list_automation_traces`, `get_automation_trace`, `get_blueprint`, and
  `check_config`.
- The compatibility-visible legacy `upsert_automation` has no direct policy and
  is refused with `governance_required` before dispatch. Governed plan apply and
  rollback are separate, narrow `direct_ha_required` operations.

Beta 10 narrows the `get_error_log` exception to the read-only
`structured_system_log_read` policy. It uses the admin-only `system_log/list`
WebSocket command and does not authorize Supervisor journal access, raw log-file
mounts, frontend scraping, or any log-triggered action. The original Phase 3C
four-read capability-truth matrix is otherwise unchanged.

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

Provider selection is metadata, not dispatch. A Standard provider known
unavailable before a call returns `provider_unavailable` and
`upstream_attempted=false`, but does not increment provider request or failure
counters. Actual dispatched failures and timeouts remain attributable exactly
once.

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

The initial facilitator phase added no public tool. The current Beta 24 catalog
contains 38 registered and 25 canonical tools with no planned capability.
Future analysis tools should depend on
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

Beta 13 records an analytical evidence item only when a reviewed deterministic basis
binds it to the target. Provider proximity and temporal overlap are not delegation or
correlation evidence. Sanitization precedes normalization and identity derivation.
Multiple rule interpretations of the same occurrence set remain distinct findings but
share a root-cause identity, preventing the facilitator from overstating incident
count. Auxiliary bounded-source retention limits remain visible without erasing
independently complete evidence.

Beta 14 requires every trace consumer to use the shared sanitized normalization
boundary. Transport success is not evidence completeness: interval timestamps must be
parsed to aware UTC instants, malformed headers must remain visible in coverage, and
only trustworthy empty results may support a no-execution evidence gap. One captured
request instant binds cutoff, result, and continuation. Cursor pages use bounded
sanitized public-output snapshots rather than repeating provider access; this is not a
reusable analytical result cache.

Beta 15 adds `change_impact_analysis`, the third analytical consumer, and increases
the manifest to 35 tools. Its Engineering provider composes the existing dependency
index with exact state/registry evidence and bounded runtime evidence. The policy is
`single_entity_change_impact_read`: no direct handler I/O, write, fallback, or Standard
MCP approximation is allowed. Unsupported static sources remain explicit coverage
gaps, so absence of findings cannot silently become a safety claim.

Beta 16 clarifies the analytical continuation contract. A signed impact cursor is
bound to a bounded sanitized snapshot only after the dependency-index refresh has
committed its final active generation. Continuations may verify that identity but
must not collect Home Assistant evidence or rebuild the index. Replaced, invalidated,
expired, mismatched, and tampered state fails closed. The snapshot exists only for
pagination and is never a reusable general result cache. Findings, unique affected
objects, and affected-object/consequence groups are separate aggregates, and
unresolved dynamic references affect assessment only when their source type was
requested and inspected.
Beta 17 applies that contract to global configuration-integrity analysis. The
Engineering provider correlates a shared dependency-index snapshot with one
bounded complete state inventory and one entity-registry inventory. The selected
policy is `global_configuration_integrity_read`; Standard HA MCP coverage is
unavailable, provider fallback is forbidden, and every unsupported source stays
visible. Candidate orphan detection never authorizes deletion or generates a
cleanup plan. See
[`../CONFIGURATION_INTEGRITY_ANALYSIS.md`](../CONFIGURATION_INTEGRITY_ANALYSIS.md).

Beta 19 adds `incident_correlation` as an Engineering-native orchestrator rather
than another general-purpose HA access tool. Policy
`bounded_incident_correlation_read` composes existing direct administrative reads
and shared dependency, integrity, and reliability services behind one bounded
internal provider. It does not recursively call public MCP tools, delegate to an
approximate Standard HA MCP capability, silently fall back, or introduce a write
path. The normalized event and hypothesis layers are transport-independent.
Every source failure stays visible; free-form logs are untrusted evidence;
temporal proximity alone cannot establish causality. See
[`../INCIDENT_CORRELATION.md`](../INCIDENT_CORRELATION.md).

Beta 20 separates provider failure from evidence completeness across analytical
coverage adapters. Successful partial evidence is a provider success with a
coverage limitation, not an upstream failure. Unsupported and not-requested sources
carry no failure category. Actual provider errors, timeouts, response-validation
errors, authentication failures, and failed item reads retain explicit failure
categories. Incident hypotheses use `missing_evidence` only for absent or failed
evidence and stable `coverage_limitations` identifiers for usable but incomplete
evidence. This preserves honest routing attribution and prevents health provider
failures from being inflated by known capability boundaries.

RC2dev4 makes compatibility enforcement explicit: legacy mutation schemas are
retained only for input compatibility, while routing rejects before their
neutered bodies, every fallback, and every Home Assistant action. Governed
proposal, external-approval request, apply, and rollback are distinct operation
classes. Standard HA MCP remains unavailable; the separately reviewed dashboard
adapter remains argument-constrained. Dependency construction uses one
single-flight shared snapshot rather than a second provider abstraction.
