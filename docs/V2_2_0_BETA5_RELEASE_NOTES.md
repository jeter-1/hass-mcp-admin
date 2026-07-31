# HA MCP Engineering Server 2.2.0-beta.5

Version `2.2.0-beta.5` adds the E1 update-and-recovery preflight foundation as
an isolated, local, runtime-inert evaluator. It consumes already-collected
evidence and does not inspect or change Home Assistant.

## Update and recovery preflight

E1 provides:

- explicit target classes for Home Assistant Core, Supervisor, Home Assistant
  OS, add-ons, HACS integrations and frontend components, the Engineering MCP
  server, upstream `ha-mcp`, and firmware update entities;
- immutable installed and candidate version evidence;
- caller-supplied `upgrade`, `downgrade`, `same`, or `unknown` version
  direction without parsing or retrieval;
- authoritative candidate and compatibility references;
- separate current repair and error evidence;
- explicit backup status, age, and location evidence;
- free and required storage evidence;
- known power-stability evidence;
- rollback and restore availability;
- expected-disruption classification;
- per-target post-update verification profiles;
- deterministic ordered blockers, warnings, and unknowns; and
- exact `ready_for_governed_planning`, `blocked`,
  `manual_review_required`, and `unsupported` verdicts.

Missing decision-critical evidence never becomes ready. Planning readiness
means only that a future governed plan may be considered; it is not approval,
authorization, scheduling, execution, or provider admission.

Only a confirmed `upgrade` may reach planning readiness. Downgrades and
unknown direction require manual review, same-version candidates are blocked
as no-op updates, and contradictory direction evidence fails closed.
Downgrade review follows
`docs/runbooks/DOWNGRADE-VERSUS-BACKUP-RESTORE.md`.

CRITICAL unresolved repairs and errors remain blockers. HIGH unresolved
repairs and errors remain warnings but now require manual review; MEDIUM and
lower severities remain informational warnings. A candidate version is
required to identify the proposed destination. A missing installed version
instead remains an unknown/manual-review condition because it prevents
confirmed direction and compatibility reasoning without proving that no
candidate exists.

## Runtime and authority boundary

This release does not add:

- startup loading or evidence collection;
- network, Home Assistant, Supervisor, update, backup, or release API access;
- MCP tools, capabilities, health fields, or provider routes;
- compatibility admission or C1/K1 runtime integration;
- change-plan, approval, execution-task, update, backup, restart, restore,
  downgrade, safe-mode, or firmware action authority;
- writes, generic actions, or fallback; or
- a runtime dependency.

The evaluator is deterministic from its explicit immutable input and does not
use a current clock, ambient runtime state, or provider state.

## Compatibility

- Source catalog: 25 canonical plus 23 Engineering-native tools, 48 local
  registered tools.
- Configured exact admission may add 26 delegated reads for an expected 74
  live tools.
- Planned tools: 0.
- Task schema version: unchanged at 1.
- Reviewed upstream recommendation: `ha-mcp` 7.14.2, protocol `2025-03-26`.
- Compatibility entry: `ha-mcp-v7.14.2-7917b2d3`.
- Catalog fingerprint:
  `c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.
- Stable v1.1.2, K1 and C1 authority boundaries, F1 execution behavior,
  approval, dispatch, provider routing, public schemas, and zero fallback are
  unchanged.

Rollback to the accepted `2.2.0-beta.4` artifact requires no update-preflight
state migration because Beta 5 loads and persists no E1 runtime state.
