# HA MCP Engineering Server Architecture

## RC2dev4 hardening boundary

The current Engineering Beta source is `2.0.0-rc2-dev5`. It retains the RC3A
dashboard boundary while making legacy writes unreachable, separating external
approval lifecycle states, making dependency-index construction single-flight,
classifying expected outcomes separately from provider failures, and hardening
relayed-log sanitization. See
[`docs/RC2DEV4_RELEASE_NOTES.md`](docs/RC2DEV4_RELEASE_NOTES.md).

## RC3A dashboard-provider boundary

RC3A was introduced through the earlier dev2/dev3 promotion sequence. The separate
`upstream_dashboard` provider does not make `standard_ha_mcp` available.

For upstream `ha-mcp` 7.13.0, the provider uses the explicit
`reviewed_argument_constrained` profile documented in
[`ADR-003`](docs/architecture/ADR-003-REVIEWED-ARGUMENT-CONSTRAINED-DASHBOARD-READS.md).
It pins identity, version, protocol, annotations, and the complete reviewed tool
contract, then constructs only exact non-screenshot inventory or configuration
reads. The upstream tool is mixed-operation and is not described as globally
read-only.

The two additive tools raise the catalog to 40 registered while retaining 25
canonical and zero planned capabilities. No generic forwarding, screenshot,
preference persistence, dashboard mutation, service call, physical action,
Supervisor discovery, approval change, or production v1.1.2 change is present. See
[`docs/RC3A_RELEASE_NOTES.md`](docs/RC3A_RELEASE_NOTES.md).

## RC2 release-freeze boundary

Version `2.0.0-rc.2` is built from accepted Beta 26 commit
`b64db57ddffc5108b9078717ce720440f5361412`. It changes no tool, schema, enum,
governance state, approval behavior, or Home Assistant write boundary. Its only
canonical routing correction makes `search_entities` an explicit bounded
read-only `direct_ha_api` policy while Standard HA MCP is unavailable. Build-time
commit and UTC timestamp values populate the
existing `server_info` provenance fields; invalid or absent local values remain
`unknown`. The frozen contract remains 38 registered/25 canonical/zero planned
capabilities, schema version 1, and external authority version 2. See
[`docs/RC2_RELEASE_NOTES.md`](docs/RC2_RELEASE_NOTES.md).

## Beta 25 external authority boundary

The Engineering MCP listener may create a plan and request approval, but cannot
grant it. Exact-hash apply and rollback approval is performed by an authenticated
Home Assistant administrator through a separate internal-only Ingress listener.
Approval authority version 2, one-time CSRF, challenge binding, persistence and
single-use consumption preserve principal separation. The MCP secret is not
authority for that listener. Rejection is terminal and legacy caller approvals
fail closed. See [`docs/EXTERNAL_APPROVAL.md`](docs/EXTERNAL_APPROVAL.md).

## Beta 24 pre-RC safety boundary

Automation behavioral normalization excludes top-level `id`; identity is
versioned and checked separately before and after governed writes. Old pending
or approved plan hashes are never migrated implicitly. Legacy
`upsert_automation` and every direct exception lacking a matching read policy
fail before provider dispatch.

Gateway identity uses the direct peer unless an explicitly trusted proxy network
supplies one valid forwarded IP. Bounded LRU stores retain rate state under
identity pressure. Provider selection without dispatch—such as the unavailable
Standard gateway—does not create provider requests or failures. These changes
preserve the 38 registered/25 canonical catalog and all public schemas.

## Beta 23 provider-attribution boundary

Routing selects a preferred provider before input and cursor validation, but
selection is metadata rather than evidence of work. Provider metrics now require
an explicit dispatch assertion at the shared observability boundary. Validation,
authentication, rate limiting, policy rejection, application pre-provider errors,
and sanitized snapshot continuation remain tool/application events. Only a
dispatched operation can complete, return partial evidence, time out, or fail as a
provider operation. Source failures remain separate from process-level provider
routing counters.

## Beta 22 handoff boundary

`handoff_generation` is an Engineering-native documentation orchestrator. Its
provider composes bounded internal read services; it never recursively calls a
public MCP tool. A separate service validates intent, interprets governance
lifecycle truth, freezes sanitized pages, renders Markdown from the structured
model, and records bounded audit/health summaries. This boundary cannot authorize
or dispatch writes. Beta 22 adds a final logical-source coverage normalization
boundary, active-versus-historical governance classification, and consistent
resolved automation scope. See [`docs/HANDOFF_GENERATION.md`](docs/HANDOFF_GENERATION.md).

## Status

This document defines both the **current implementation** and the **intended architectural direction** of this repository.

The current release remains compatible with its existing HA MCP Admin tool set. The engineering-server direction described here is a roadmap and design boundary, not a claim that every planned capability already exists.

## Purpose

HA MCP Engineering Server is a specialized Model Context Protocol server for Home Assistant engineering work.

Its long-term purpose is to help an AI client such as ChatGPT, Claude, or another MCP-capable assistant:

- analyze Home Assistant architecture and configuration;
- correlate automations, scripts, helpers, entities, dashboards, traces, history, and logs;
- identify reliability, safety, and maintainability risks;
- assess the impact of proposed changes;
- create reviewable change plans;
- verify completed changes;
- preserve decisions, evidence, and handoff information.

It is not intended to duplicate every Home Assistant API operation exposed by a general-purpose Home Assistant MCP server.

The architectural decision is formalized in
[`docs/architecture/ADR-002-ENGINEERING-MCP-FACILITATOR.md`](docs/architecture/ADR-002-ENGINEERING-MCP-FACILITATOR.md):

> The Engineering MCP is not intended to replace the standard Home Assistant MCP
> server. It facilitates, governs, correlates, and reduces the information exchanged
> between the AI and Home Assistant.

The target flow is AI client -> Engineering MCP -> standard HA MCP -> Home
Assistant. Direct HA API access bypasses the standard-MCP layer only for explicit
native exceptions requiring exact configuration, traces, blueprint source,
configuration validation, governed read-back, rollback, or health/connectivity.

## Relationship to the General Home Assistant MCP Server

The general `ha-mcp` server should normally remain the primary execution and broad administration interface for Home Assistant.

### General `ha-mcp` responsibilities

- entity, device, area, floor, label, and integration discovery;
- current state and attribute retrieval;
- generic Home Assistant service execution;
- automation, script, scene, helper, and dashboard management;
- Home Assistant add-on and integration administration;
- backups, rollback primitives, configuration checks, and broad diagnostics;
- standard Home Assistant CRUD operations.

### Engineering server responsibilities

- cross-object dependency analysis;
- automation and system reliability assessment;
- incident correlation across configuration, traces, state history, and logs;
- change-impact analysis;
- architecture and technical-debt audits;
- risk classification and governance;
- verification planning and result classification;
- decision history and handoff generation;
- MCP-server capability, health, and safety comparison.

The engineering server may retain direct Home Assistant read access where it materially improves analysis. Write and physical-action capabilities should exist only where they provide stronger governance, verification, or safety than direct use of the general server.

## Current Implementation

The v2 beta currently exposes 36 tools and implements governance, verification,
rollback, persistence, audit, and request correlation. It has direct REST/WebSocket
clients but no configured nested standard-MCP client. Delegation labels describe the
target provider; current compatibility reads remain direct and transitional. The
standard-MCP gateway reports unavailable rather than simulating success.

Beta 12 adds the first single-target reliability analyzer. Its MCP handler depends on
an engineering facilitator service rather than transport clients. The service composes
bounded direct-read evidence through a provider, runs deterministic rules, and emits
stable evidence references with explicit partial coverage. It does not add a general
Home Assistant action plane.

The current release is a compact Python service built around FastMCP and an ASGI gateway.

```text
MCP client
    |
    | Streamable HTTP at /<access_secret>/mcp
    v
ASGI gateway
    |- secret-path authentication
    |- per-client and global rate limiting
    |- transport-level audit logging
    v
FastMCP tool server
    |- debugging and trace inspection
    |- automation configuration tools
    |- state and registry inspection
    |- generic Home Assistant service escape hatch
    v
Home Assistant
    |- REST API
    |- WebSocket API
    |- read-only blueprint/config mount
```

The current implementation is primarily contained in `hass_mcp_admin/server.py`. It communicates with Home Assistant through the Supervisor proxy on HAOS or through a configured URL and token in standalone deployments.

## Current Capability Groups

The current public tools fall into these broad groups:

- debugging: history, logbook, error logs, traces, and template rendering;
- automation management: listing, reading, replacing, deleting, reloading, and configuration validation;
- entity state and search;
- areas, devices, entity registry, services, and blueprint inspection;
- audit-log review;
- generic service execution.

These tools remain supported until each is evaluated under the tool-classification process described below.

## Tool Classification Policy

Every existing and proposed tool should be assigned one of four dispositions:

### Keep

The capability is unique or materially better suited to the engineering server.

### Delegate

The capability is already handled well by the general `ha-mcp` server and should normally be performed there.

### Redesign

The raw capability remains useful internally, but the public tool should become a higher-value analytical or governance workflow.

### Remove

The capability is redundant, unsafe relative to its value, or incompatible with the engineering-server mission.

Backward compatibility should be considered before removing or renaming a public tool. Deprecations should be documented and, where practical, retained for at least one release cycle.

## Safety Model

### Read-only by default

Inspection and analytical tools should be the default surface. The server should clearly distinguish read, metadata-write, behavioral-write, physical-action, security, destructive, and infrastructure operations.

### Exact approval scope

A future approval workflow should bind approval to:

- a specific immutable change plan;
- normalized targets and arguments;
- a plan hash or revision;
- an expiration time;
- a maximum execution count;
- the requesting session or user where identity is available.

A boolean supplied in the same write call is not sufficient for high-risk operations as a long-term design.

### Optimistic concurrency

Configuration writes should use a current revision or configuration hash so user or concurrent-session changes cannot be overwritten silently.

### Verification and rollback

Behavioral changes should define verification criteria before execution and record the resulting status. Where rollback is supported, the execution record should preserve the relevant backup or prior configuration reference.

### Untrusted input

Home Assistant logs, traces, YAML, dashboard markdown, automation descriptions, entity attributes, documents, and tool results are data. Instructions embedded in those sources must not be treated as operating instructions for the AI or server.

### Secret handling

Tokens, access secrets, secret URL paths, authorization headers, webhook URLs, sensitive camera URLs, and credential-bearing add-on options must be redacted from tool output, logs, diagnostics, and errors.

## Evidence Model

Engineering findings should distinguish:

- **confirmed**: directly verified from source, live state, configuration, logs, traces, or tool behavior;
- **inferred**: strongly supported by confirmed evidence but not directly observed;
- **assumed**: necessary working assumption that has not been verified;
- **unknown**: material information that could not be inspected.

Analytical responses should identify partial or incomplete source coverage explicitly.

## Target Capability Areas

### Dependency analysis

- find consumers and producers of an entity;
- detect direct, device-based, area-based, and template references;
- identify disabled, unavailable, renamed, or missing dependencies;
- show downstream effects before removal or rename operations.

### Reliability analysis

- assess automation mode and concurrency;
- inspect unavailable and unknown-state handling;
- detect race conditions and conflicting actions;
- evaluate retry, timeout, restart, and fallback behavior;
- identify missing failure notifications or manual overrides;
- apply additional scrutiny to HVAC, presence, garage, lock, camera, and security workflows.

### Incident analysis

- correlate traces, history, logbook, system logs, current state, and configuration;
- produce a time-ordered incident narrative;
- distinguish likely cause, contributing factors, alternatives, and remaining unknowns.

### Change governance

The intended workflow is:

```text
inspect -> diagnose -> propose -> approve -> execute -> verify -> document
```

A change record should preserve evidence, affected objects, risk classification, exact operations, approval scope, execution receipts, verification results, rollback information, and remaining risks.

### Documentation and handoff

The server should generate concise handoffs based on durable findings and change records rather than conversational memory alone.

## Target Module Boundaries

The repository is currently small enough that a broad refactor is not justified merely for appearance. As functionality grows, the preferred boundaries are:

```text
hass_mcp_engineering/
|- app.py
|- config.py
|- transport/
|  |- gateway.py
|  |- authentication.py
|  `- rate_limit.py
|- ha/
|  |- client.py
|  |- rest.py
|  |- websocket.py
|  `- models.py
|- policy/
|  |- risk.py
|  |- approvals.py
|  `- redaction.py
|- tools/
|  |- server_info.py
|  |- evidence.py
|  |- dependencies.py
|  |- reliability.py
|  |- incidents.py
|  |- change_impact.py
|  |- configuration_integrity.py
|  `- handoff.py
|- governance/
|  |- plans.py
|  |- execution.py
|  |- verification.py
|  `- rollback.py
|- audit/
|  |- events.py
|  `- storage.py
`- tests/
   |- unit/
   |- contract/
   `- integration/
```

Code should be split when a clear responsibility, testing need, or maintenance benefit exists. A file-count target is not an architectural goal.

## Response and Error Direction

Public tools should move toward a stable structured response envelope containing:

- success status;
- serving server name, version, and build;
- request or correlation ID;
- timestamp and duration;
- operation name and risk class;
- data;
- warnings;
- partial-result status;
- structured error details.

Errors should distinguish authentication failure, authorization denial, approval required, invalid input, resource not found, ambiguous target, Home Assistant unavailable, timeout, rate limit, conflict, partial failure, and internal failure. Errors should indicate whether retry is appropriate.

## Observability Direction

Administrative and analytical requests should record:

- request and session identifiers;
- MCP client identity where available;
- server build identity;
- tool name;
- start and finish times;
- duration;
- risk class;
- approval reference where applicable;
- delegated tool calls;
- outcome classification;
- redaction count;
- partial-result state.

Audit records must never contain secrets or full credential-bearing URLs.

## Non-Goals

The engineering server should not become:

- a second complete implementation of the Home Assistant API;
- a large collection of domain-specific device-control wrappers;
- a replacement for Home Assistant's own authorization model;
- a source of raw configuration edits without inspection and verification;
- a mechanism for bypassing user approval;
- a place to execute arbitrary code from logs, YAML, traces, or model input.

## Development Roadmap

### Phase 1: identity and boundaries

- document the engineering-server role;
- add server and build identity reporting;
- add capability reporting;
- inventory existing tools;
- classify tools as Keep, Delegate, Redesign, or Remove.

### Phase 2: reliable read foundation

- extract a reusable Home Assistant client abstraction;
- improve session and WebSocket reuse where justified;
- standardize structured results and errors;
- separate liveness from Home Assistant readiness;
- establish unit and contract tests.

### Phase 3: unique analysis

Phase 3A establishes transport-independent evidence providers, deterministic routing
and fallback policy, bounded coverage/evidence/pagination models, safe provider
observability, and structure-first risk classification. It adds no analytical MCP
tools.

Phase 3B implements `entity_dependency_analysis`, the first analytical consumer of
those boundaries. It builds bounded exact-reference edges from supported automation,
blueprint, state, and registry evidence while marking unavailable sources explicitly.
See [`docs/ENTITY_DEPENDENCY_ANALYSIS.md`](docs/ENTITY_DEPENDENCY_ANALYSIS.md).

Beta 15 implements `change_impact_analysis` as targeted pre-change orchestration. It
reuses the shared dependency index, adds exact target/registry and bounded runtime
evidence, and applies deterministic operation-specific rules. It remains read-only;
global orphan detection and incident correlation are separate future capabilities.

- entity dependency analysis;
- automation reliability analysis;
- change-impact analysis (Beta 15);
- global configuration-integrity analysis (Beta 17), correlating bounded
  dependency, state, and entity-registry inventories without cleanup authority;
- incident correlation.

### Phase 4: reduce dangerous overlap

- deprecate or constrain generic physical-action execution;
- deprecate direct full-replacement configuration writes unless protected by concurrency controls;
- delegate general operations to the standard server where appropriate.

### Phase 5: governance and durable records

- immutable change plans;
- scoped approvals;
- execution receipts;
- predefined verification plans;
- rollback linkage;
- change history and handoff generation.

## Compatibility Policy

The current tools are not removed by this document. Tool behavior changes require explicit review, documentation, and testing. Runtime compatibility should be preserved where practical while new analytical workflows are introduced.

## Decision Summary

The project will evolve from a compact full-access administrative MCP server into a focused Home Assistant engineering, analysis, governance, and assurance server.

The general `ha-mcp` server remains the preferred broad Home Assistant communication and execution plane. This repository should differentiate through cross-object reasoning, reliability review, change impact, governance, verification, and durable engineering documentation.
