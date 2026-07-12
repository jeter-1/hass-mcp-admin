# HA MCP Engineering Server v2 Beta Architecture

## Production and beta boundaries

The repository contains two independently installable Home Assistant add-ons.

| Property | Production v1 | Engineering v2 beta |
| --- | --- | --- |
| Directory | `hass_mcp_admin/` | `hass_mcp_engineering_beta/` |
| Name | HA MCP Engineering Server | HA MCP Engineering Server Beta |
| Slug | `hass_mcp_admin` | `hass_mcp_engineering_beta` |
| Version | `1.1.2` | `2.0.0-beta.7` |
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
   |- routing.py            # deterministic capability and fallback policy
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

## Compatibility approach

The beta's compatibility module preserves the current 25 function names,
argument schemas, docstrings, and implementations. Transport calls are routed
through the v2 REST and WebSocket client boundaries, response serialization
through the response-model boundary, and server construction through the v2
FastMCP factory. `server_info` and `list_capabilities` use beta version and
capability metadata.

The v1.1.2 catalog currently contains 8 native, 10 transitional, 4 delegated,
and 3 deprecated tools. Beta 7 advertises 4 remaining planned capabilities. The often
quoted transitional count of 9 is inconsistent with the checked-in 25-tool
catalog; v2 intentionally preserves the source catalog rather than
reclassifying a tool during a scaffold change.

## Future extension boundaries

Phase 3A implements the provider and response boundaries defined in
[`docs/architecture/ADR-002-ENGINEERING-MCP-FACILITATOR.md`](docs/architecture/ADR-002-ENGINEERING-MCP-FACILITATOR.md).
There is currently no supported nested standard-HA-MCP transport. The standard
provider is deliberately unavailable; existing compatibility reads remain direct until
a future migration. Provider availability must not be inferred from lifecycle labels.

The response and error models are intentionally minimal. Future changes can
add structured envelopes, dry-run results, approval state, rollback metadata,
change governance, analysis findings, relationship graphs, orphan detection,
and trace comparison without coupling those concepts to the transport or
gateway.

The response, error, audit, request-correlation, structured logging, timing, and
health foundations are documented in
[`hass_mcp_engineering_beta/OBSERVABILITY.md`](hass_mcp_engineering_beta/OBSERVABILITY.md).
They are active for a representative beta tool set while compatibility tools
continue to use their existing response formats.

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

Beta 7 adds one engineering-native dependency-analysis tool and changes the callable
manifest to 33 tools. Exact source coverage is documented in
[`docs/ENTITY_DEPENDENCY_ANALYSIS.md`](docs/ENTITY_DEPENDENCY_ANALYSIS.md).

- The scaffold deliberately preserves legacy tool response formats.
- Structured envelopes and governance features are boundaries only, not active
  behavior.
- The 23 non-foundation tools still use compatibility implementations.
- Live Home Assistant behavior requires a Supervisor token or an explicit
  standalone `HA_URL`/`HA_TOKEN` pair.
- Standard Home Assistant MCP delegation is not operational in Beta 7; the gateway
  reports explicit unavailability and never fabricates delegated evidence.
