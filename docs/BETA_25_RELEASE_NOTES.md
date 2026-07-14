# Beta 25 release notes

Version: `2.0.0-beta.25`

Beta 25 makes governed approval an external human action. An authenticated MCP
caller can create a plan and request review, but cannot transition the plan to
approved. `approve_change_plan` keeps its public input schema and now returns
`approval_pending` for review in the Home Assistant administrator-only Ingress
panel. It does not grant authority.

- The Ingress panel uses an internal-only port (`8110`), `panel_admin`,
  server-rendered escaped HTML, POST-only decisions, and one-time CSRF nonces.
- Challenges are random, persisted, 15-minute maximum, idempotent while active,
  single-use, and bound to the exact plan/version/hash/kind/target/operation/risk.
- Apply and rollback require separate authority-version-2 Ingress approvals.
- Rejected plans are terminal and cannot be reopened.
- Active pre-Beta-25 approvals are legacy and must be recreated; they are never
  silently upgraded. Terminal history remains readable.
- Beta 24 normalization version 2, id-less verification, trusted-proxy safety,
  bounded rate-limit eviction, provider accounting, legacy-write refusal and
  audit bounds remain intact.
- CI adds a blocking disposable Home Assistant Core `2026.7.2` contract stage,
  pinned to an immutable image digest. It verifies the actual REST/WebSocket,
  automation readback, configuration-check and trace contracts.
- Human approval is not proof of future automation behavior. Beta 25 adds no
  behavioral observation, mobile notification, new write tool, service call,
  background monitoring, or general administrative UI.

The public catalog remains 38 registered tools, 25 canonical tools, zero planned
capabilities and schema version 1. No tool name, input schema or input enum was
changed. The Beta slug and MCP port remain `hass_mcp_engineering_beta` and
`8100`. Production v1.1.2 (`hass_mcp_admin`, port `8099`) is unchanged.

See [`EXTERNAL_APPROVAL.md`](EXTERNAL_APPROVAL.md) for the trust model, migration,
audit/health contract, real-HA CI stage, and the complete user-run deployed
acceptance procedure.
