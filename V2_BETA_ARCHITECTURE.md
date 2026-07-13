# HA MCP Engineering Server v2 Beta Architecture

## Production and beta boundaries

The repository contains two independently installable Home Assistant add-ons.

| Property | Production v1 | Engineering v2 beta |
| --- | --- | --- |
| Directory | `hass_mcp_admin/` | `hass_mcp_engineering_beta/` |
| Name | HA MCP Engineering Server | HA MCP Engineering Server Beta |
| Slug | `hass_mcp_admin` | `hass_mcp_engineering_beta` |
| Version | `1.1.2` | `2.0.0-beta.17` |
| Port | `8099` | `8100` |
| Options and secret | Production add-on data | Beta add-on data |

Home Assistant derives a distinct internal service/DNS name from each add-on
slug and repository identifier. The beta slug therefore produces a hostname
ending in `hass-mcp-engineering-beta`; it cannot collide with production's
hostname. Each container has its own `/data/options.json`, access secret, audit
file, process, and published port.

The v2 beta never imports or mutates production configuration. Production
v1.1.2 remains the rollback target and continues to run from its existing
directory and port.

## Module structure

Phase 3A adds these beta-only internal boundaries without changing the MCP registry:

```text
ha_mcp_engineering/
|- facilitation/models.py   # bounded result, evidence, pagination, and coverage
`- providers/
   |- base.py               # transport-independent evidence provider interface
   |- models.py             # provider request, result, error, and coverage models
   |- routing.py            # deterministic capability and tool-exception policy
   |- dispatch.py           # schema-preserving canonical provider dispatch
   |- standard_mcp.py       # honest unavailable delegation boundary
   `- direct_ha.py          # explicit direct-API exception boundary
```

```text
hass_mcp_engineering_beta/
├── config.yaml                         # isolated Home Assistant add-on metadata/options
├── Dockerfile                          # beta image entry point and port
├── requirements.txt                    # exact pinned runtime dependencies
└── ha_mcp_engineering/
    ├── __main__.py                     # process entry point
    ├── application.py                  # composition, validation, Uvicorn startup
    ├── mcp_server.py                   # FastMCP construction
    ├── routing.py                      # secret-path auth, normalization, rate limiting
    ├── configuration.py                # beta options and environment loading
    ├── audit.py                        # secret-safe audit records
    ├── capabilities.py                 # tool and planned-capability catalog
    ├── version.py                      # beta identity/build metadata
    ├── errors.py                       # application exception boundary
    ├── clients/
    │   ├── rest.py                     # Home Assistant REST transport
    │   └── websocket.py                # Home Assistant WebSocket transport
    ├── models/
    │   ├── responses.py                # future structured-response boundary
    │   └── failures.py                 # future structured-error boundary
    └── tools/
        ├── registry.py                 # registration boundary
        └── compatibility.py            # unchanged v1.1.2 tool behavior/signatures
```

Beta 11 adds `ha_mcp_engineering/sanitization.py` as the centralized recursive
trust-boundary for System Log results plus beta log/audit contexts. It returns sanitized
data and bounded category/count telemetry; tool code does not implement parallel
redaction rules.

Beta 12 adds `ha_mcp_engineering/reliability/` for provider-backed evidence
collection, deterministic rules, bounded models, pagination, orchestration, and
runtime composition. The tool handler remains transport-independent.

Beta 14 adds `ha_mcp_engineering/trace_normalization.py` as the common sanitized
boundary for the canonical `list_automation_traces` tool and Engineering reliability
collection. The pure normalizer owns interval/scalar timestamp parsing, UTC ordering,
run-ID deduplication, and header bounds; the reliability provider alone owns lookback
and detail-selection policy. One injected request clock binds the analysis cutoff and
result metadata. Cursor continuation uses only bounded sanitized public output and
does not create a reusable result cache.

Beta 15 adds `ha_mcp_engineering/impact/` for a transport-independent single-entity
impact provider, bounded evidence/result models, deterministic rules, signed
pagination, and orchestration. It reuses the dependency index and trace sanitizer;
the MCP handler does not perform Home Assistant I/O and the new capability has no
write, fallback, or Standard MCP path.

Beta 16 hardens that boundary without adding a capability. Pagination snapshots
are committed only against the active dependency-index generation and are
five-minute, process-local continuation state—not a general result cache. Cursor
pages synchronously verify the committed identity but cannot trigger evidence
collection or an index rebuild. Impact findings, unique affected objects, and
affected-object/consequence root-cause groups are separate contracts and metrics.
Requested-scope unresolved dynamic references conservatively require review;
unrequested source types do not create false review requirements.

## Compatibility approach

The beta's compatibility module preserves the current 25 function names,
argument schemas, docstrings, and implementations. Transport calls are routed
through the v2 REST and WebSocket client boundaries, response serialization
through the response-model boundary, and server construction through the v2
FastMCP factory. `server_info` and `list_capabilities` use beta version and
capability metadata.

The v1.1.2 catalog contains 8 native, 10 transitional, 4 delegated, and 3 deprecated
tools. Beta 9 truthfully reclassifies the four administrative reads as transitional,
producing 8 native, 14 transitional, and 3 deprecated canonical tools. Beta 15 has
10 additive beta-native tools and advertises 2 remaining planned capabilities. The often
quoted transitional count of 9 is inconsistent with the checked-in 25-tool
catalog; v2 intentionally preserves the source catalog rather than
reclassifying a tool during a scaffold change.

## Future extension boundaries

Phase 3A implements the provider and response boundaries defined in
[`docs/architecture/ADR-002-ENGINEERING-MCP-FACILITATOR.md`](docs/architecture/ADR-002-ENGINEERING-MCP-FACILITATOR.md).
The verified Standard HA MCP endpoint is `/api/mcp`, available to an add-on through
`http://supervisor/core/api/mcp` with its Supervisor bearer token. Its selected Assist
API does not expose exact equivalents for entity-ID lookup, the area registry, or the
service catalog. Beta 9 does not configure or call that endpoint. The four exact
administrative reads enter facilitator dispatch as explicit `transitional_direct`
exceptions. Provider availability must not be inferred from lifecycle labels.

The response and error models are intentionally minimal. Future changes can
add structured envelopes, dry-run results, approval state, rollback metadata,
change governance, analysis findings, relationship graphs, orphan detection,
and trace comparison without coupling those concepts to the transport or
gateway.

The response, error, audit, request-correlation, structured logging, timing, and
health foundations are documented in
[`hass_mcp_engineering_beta/OBSERVABILITY.md`](hass_mcp_engineering_beta/OBSERVABILITY.md).
They are active for beta-native tools and every provider-routed canonical tool.

## Tool migration rules

1. Preserve the public tool name and generated argument schema unless a
   separately reviewed breaking change is approved.
2. Capture parity tests before moving a function out of `compatibility.py`.
3. Move one coherent tool family at a time into a focused module.
4. Keep Home Assistant I/O behind `clients/` and response construction behind
   `models/`.
5. Preserve capability classification unless the migration explicitly changes
   lifecycle status and documents why.
6. Add behavior-level regression tests before deleting the compatibility
   implementation.
7. Do not share production options, secrets, audit state, or runtime ports.
8. Keep destructive-action confirmation behavior unchanged until governance
   and approval semantics are implemented and reviewed.
9. New analytical code depends on `EngineeringEvidenceProvider`, never directly on a
   REST, WebSocket, or nested-MCP transport.
10. A standard-MCP failure never falls back to an ungoverned direct write. Permitted
    direct read fallback requires central policy and explicit request intent.
11. Use the bounded response contract in [`docs/TOKEN_EFFICIENCY.md`](docs/TOKEN_EFFICIENCY.md).

## Known limitations

Beta 15 retains dependency and reliability analysis and adds single-entity
change-impact analysis, bringing the callable manifest to 35 tools. Exact impact
source coverage is documented in
[`docs/CHANGE_IMPACT_ANALYSIS.md`](docs/CHANGE_IMPACT_ANALYSIS.md).

- Provider-routed canonical tools now return the facilitator response envelope.
- Handler bodies remain compatibility implementations behind the routing boundary.
- Live Home Assistant behavior requires a Supervisor token or an explicit
  standalone `HA_URL`/`HA_TOKEN` pair.
- Standard Home Assistant MCP transport is verified but has no approved exact mapping
  in this release; the gateway reports explicit unavailability and is not called.
- `GetLiveContext` is not an exact replacement for entity, registry, or service-catalog
  administrative reads.
Beta 17 adds `configuration_integrity_analysis` as a fourth Engineering-native
analytical consumer. It reuses the same dependency-index generation and Beta 16
snapshot lifecycle, adds one complete state and entity-registry inventory per
new analysis, and classifies integrity evidence locally. Its orphan findings are
candidates for review only; it has no registry, configuration, service, plan,
reload, or restart write path. The callable manifest is 36 tools.
