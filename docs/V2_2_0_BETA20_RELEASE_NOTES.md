# HA MCP Engineering Server 2.2.0-beta.20 release notes

Beta 20 is based directly on merged Beta 19 main
`51943e11cc5290b1bf8db75474982193463044f5`. It activates the accepted F3
shared executor, durable child ownership, and cross-process locks for existing
governed configuration and operational apply routes.

Automation, script, `input_boolean`, and `input_number` create/update use the
accepted F3-C1 adapters. Full backup, controlled reload, exact installed add-on
restart, and Home Assistant restart use the accepted F3-C2 adapter. Each
operation computes complete locks, repeats exact preflight, consumes existing
approval, commits F3 intent with one reserved dispatch, invokes one fixed
provider mutation, reads back, verifies, and recovers without blind redispatch.

One schema-1 task remains the compatible public projection. A cross-process
journal binds it to deterministic F3 children and prevents legacy/F3 dual
authority. Existing legacy tasks retain their original read-only reconciler;
no active task is silently migrated or converted and no F3 task falls back.

One coordinator completes a startup sweep before listener readiness and runs
every 30 seconds. It processes at most 16 tasks and one transition per task
within a five-second budget, uses persisted backoff, preserves immutable
deadlines, transfers only fenced stale locks, and invokes only observation and
verification after durable intent.

Manual review atomically retains only the unresolved target while releasing
dependencies. Holds do not expire. The existing authenticated private Ingress
surface binds administrator identity, CSRF, prepared hash, record generation,
and fencing generations for bounded observation, verification, retention/
release, or governed rollback-plan creation. No public reconciliation tool is
added.

Configuration rollback uses Option A: a request creates a separate reverse
update plan and performs no immediate Home Assistant mutation. The plan needs
its own approval and executes through the same F3 locks, durable intent, one
write, and exact readback. Operational and dashboard rollback are unavailable.

Dashboard execution remains deferred. Reviewed Home Assistant/`ha-mcp`
interfaces still lack authoritative compare-and-save or exclusion of all other
writers. Beta 20 registers no dashboard planning tool or execution capability,
invokes no setter, adds no `update_dashboard` operation, and never executes
generated `python_transform`. A final reread is not claimed to prove that an
already overwritten third-party edit never occurred.

No public tool is added. Engineering-local accounting remains 25 canonical plus
23 Engineering-native, or 48. Exact 7.14.2 remains 78 advertised, 26 delegated,
zero held, and 74 configured total. Exact 8.0.0 remains 78 advertised, 24
delegated, two held, and 72 total. Held tools remain exactly `ha_search` and
`ha_get_operation_status`; fallback remains zero.

Protocol `2025-03-26`, task schema 1, configuration plan contract 2,
operational plan contract 3, approval authority, F2 policy, exact release
admission, Dashboard v3 reads, `aiohttp==3.14.3`, `cryptography==50.0.0`, and
stable 1.1.2 are unchanged.

Downgrade to Beta 19 is routine only if Beta 20 has never created an F3
execution. Terminal isolated records stay retained for audit. A nonterminal F3
execution or hold must be reconciled in Beta 20 before downgrade; older code
must not claim or redispatch that work.

Development and acceptance used only synthetic, disposable, and CI fixtures.
No production Home Assistant or deployed MCP endpoint was accessed. Nothing
was deployed, restarted, tagged, released, published, or merged by this track.
