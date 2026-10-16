# Engineering 2.4.1-beta.11 release notes

Engineering 2.4.1-beta.11 is a dependency security release (HAMCP-188). It
upgrades the pinned MCP Python SDK (`mcp`) from 1.28.1 to 1.30.0. That fixes
GHSA-84m7-p3x7-pcfv / CVE-2026-59951, which `pip-audit --strict` reports
against 1.28.1. It builds on beta.10's truthful dispatch facts (HAMCP-181).
Josh chose the latest 1.30.x patch on the same major line; 2.x is out of
scope. No Engineering runtime code, public schema, tool registration,
provider route or policy changes.

## Advisory

In the stateful Streamable HTTP session manager, `mcp` 1.8.0 through 1.29.1
(and 2.0.0 through 2.1.1) never reclaimed session state:
- sessions had no expiry by default;
- DELETE left the session registered;
- a refused opening request stayed registered with a running task;
- the number of sessions was unbounded.

1.30.0 and 2.2.0 fix this. Stateless mode on 1.12.0 or later, and stdio or SSE
alone, are not affected.

Engineering runs the SDK's Streamable HTTP server behind
`AuthenticatedMcpGateway`, in two modes:
- **Stateless** (the default, with the ha-mcp release registry disabled): not
  affected by the advisory.
- **Stateful** (registry enabled): affected on 1.28.1. Engineering already
  limited this with its own 1,024-session cap (503), a 1,800-second idle
  timeout and DELETE cleanup. A refused opening request still left a
  registered session and task behind until the idle timeout reclaimed it.

The SDK's other components are either unaffected or unused here: the
outbound Streamable HTTP client, server auth, websocket transports and
experimental task handlers.

## Dependency change

Only `mcp` changes: 1.28.1 to 1.30.0.
- `requirements.txt`, both locks and `build-inputs.json` change, the locks
  with the reviewed py3-none-any wheel hash
  `sha256:666edb5009503e1047c9d60346a756f94b261f05cc2625f23d41c728ffc484d0`.
- 1.30.0 declares the same `Requires-Dist` as 1.28.1, so the other 38 runtime
  packages and every test-only package are unchanged.
- The reviewed SDK guard (`PINNED_MCP_SDK_VERSION`) now admits only 1.30.0.
  Any other installed version still refuses startup.

## SDK behaviour changes Engineering inherits

Engineering adapts none of these. Each is disclosed so Josh can accept it.

1. **Stateful only: a refused opening request is reclaimed immediately.** A
   request with no session ID that the SDK answers with 400 or more no longer
   leaves a session or task behind. Examples are a batch, malformed JSON or
   a non-initialize request. It no longer counts against Engineering's
   1,024-session cap.
2. **Stateful only: idle means no request in flight.** A session with an open
   GET stream or a long-running request is no longer expired 1,800 seconds
   after its last request arrived. It is reclaimed 1,800 seconds after its
   last request completes. Engineering's 1,024-session cap still bounds the
   total.
3. **Stateful only: DELETE is reclaimed by the SDK itself.** Engineering's
   post-DELETE cleanup is now a no-op safeguard.
4. **SDK request-body limit, 4 MiB, 413.** For POST, Engineering's gateway
   refuses bodies above 2,000,000 bytes first, so the SDK's limit applies
   only to a GET or DELETE with a body larger than 4 MiB.
5. **SDK session cap, 10,000, 503.** Engineering's 1,024 cap is reached first.
6. **Outbound client: redirects stay on the same origin.** Every SDK
   Streamable HTTP client that Engineering opens to ha-mcp follows a redirect
   only when it keeps the method (307/308 for POST), adds no userinfo, and
   either stays on the endpoint's origin (scheme, host and port) or is a
   same-host, default-port HTTP→HTTPS upgrade. It ignores `follow_redirects`. This covers the dashboard read
   (`clients/mcp.py`), the delegated reads (`clients/upstream_read.py`) and
   dashboard analysis (`clients/dashboard_analysis.py`).
   - 1.28.1's default client followed every redirect, including one to another
     origin, and re-sent the request body there.
   - On 1.30.0 a cross-origin redirect (another host or port, a downgrade,
     or added userinfo) fails as a refusal Engineering classifies as
     `upstream_error`. The SDK's same-host default-port HTTP→HTTPS upgrade is
     the one permitted scheme change.
   - Dashboard analysis's fenced transport still refuses any URL other than
     the exact endpoint.
7. **Outbound client output-schema validation** resolves `$ref` only within
   the tool's own schema. No reviewed descriptor uses an external `$ref`.

Unchanged: JSON-RPC batch refusal, inbound protocol negotiation, protocol
constants, all 59 native `tools/list` descriptors (byte-identical), every
delegated descriptor, fingerprint and admission decision, the stateless
topology, gateway authentication, the generation gate, cancellation, and the
HAMCP-175 truthful-refusal handling across SDK task groups.

## Preserved

All of the following are unchanged:
- the public catalog of **87 tools (59 native, 28 delegated)**, the Core
  profiles, probe and signed fingerprints, the signed ha-mcp registry, the
  8.6.0 HOLD and the support window;
- storage formats, workflows and pinned runners;
- stable v1.1.2.

Stable v1 (`hass_mcp_admin/`) pins `mcp==1.9.0` with `stateless_http=True`.
That version is inside the advisory's affected range and below its 1.12.0
stateless exemption. It is frozen and retired, so it is not changed here.
Josh needs to decide separately what to do about it.

Rollback: reinstall beta.10. Nothing new is persisted.
