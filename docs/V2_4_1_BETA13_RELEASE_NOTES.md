# Engineering 2.4.1-beta.13 release notes

Engineering 2.4.1-beta.13 makes the Core gateway fail closed for any tool
with no Core requirement mapping (HAMCP-164). It builds on beta.12
(HAMCP-184).

No tool, input schema, description, provider route, signed registry, HOLD,
support window, persisted format, workflow or stable-v1 change is included. No
new write, dispatch, service-call or fallback path is reachable. The design
note is
[`HAMCP_164_CORE_GATE_FAIL_CLOSED_DESIGN.md`](HAMCP_164_CORE_GATE_FAIL_CLOSED_DESIGN.md).

**Unmapped tools are refused.** Before beta.13, the gateway skipped the Core
check for any tool whose Core requirement set was empty, so a missing mapping
silently meant "Core-independent". Now every name that reaches the gateway is
exactly one of:
- **mapped**: Core requirements are acquired before the tool runs (unchanged);
- **Core-independent**: an exact entry in a closed allowlist (unchanged
  behavior);
- **delegated**: a currently published read-gateway read, which acquires its
  own Core requirements (unchanged);
- **unmapped**: refused.

The refusal happens before Core acquisition and before any provider, upstream
or Home Assistant call:
- an MCP tool result with `isError: true` (as for an unknown tool on
  beta.12), carrying error `CoreRequirementUnmapped`, error code
  `unsupported_operation`, not retryable;
- reason `core_requirement_unmapped`;
- `core_authority_acquired: false`;
- zero-attempt telemetry: no provider dispatch, no upstream or Home Assistant
  request.

The refusal is audited with argument field names only, never values.

**Closed allowlist.** Sixteen native tools are Core-independent, each with a
recorded basis and justification:
- `get_core_log_history`: Supervisor only;
- `get_blueprint`: mounted read-only source;
- eight tools on Engineering local state only;
- six tools that acquire their own exact typed or F3 Core authority at dispatch.

`server_info` and `get_server_health` stay Core-independent only with
`check_ha=false`.

The allowlist is immutable and matched by exact name only: no prefix, wildcard
or case folding. The module refuses to import when the allowlist or
requirement map is ambiguous.

**No working tool changes behavior.** The only visible difference is for
names that are not working tools: an unknown or withdrawn tool name now
receives `CoreRequirementUnmapped` from the gateway instead of the MCP SDK's
`Unknown tool` error.

The healthy catalog remains **87 tools: 59 native and 28 delegated reads**.

**Also included (documentation and comments only):**
- stale MCP SDK version comments now name the pinned 1.30.0;
- the HAMCP-188 session-contract test docstring now states the correct
  expected-failure count on mcp 1.28.1;
- the beta.11 notes, acceptance and changelog wording now permit a same-host
  default-port HTTP→HTTPS upgrade.

Downgrade: beta.12 reads every beta.13 record, since no persisted format
changes. A downgraded server again treats a missing Core mapping as
Core-independent, and returns the SDK `Unknown tool` error for unknown names.

Follow the [beta.13 acceptance contract](V2_4_1_BETA13_ACCEPTANCE.md).
