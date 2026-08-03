# HA MCP Engineering Server 2.2.0-beta.11 release notes

## Release boundary

Beta 11 contains two focused corrections: bounded restart reconciliation and
exact reviewed `ha-mcp` 8.0.0 compatibility alongside 7.14.2. It does not add
an Engineering-local tool, provider, write, protocol, policy class, fallback,
or stable-v1 change. Delta-aware safety policy and F3 remain deferred.

## Restart reconciliation

Beta 10 could revisit stale persisted restart work at an approximately
47-second cadence and perform expensive probes without a path to valid new
evidence. Beta 11 uses the durable task's original
`maximum_post_dispatch_deadline` as an immutable stop. Historical taskless
plans derive the same limit from the persisted dispatch time and the existing
maximum interval; a missing trustworthy timestamp terminalizes in manual
review without a network probe.

The scheduler now evaluates a local persisted eligibility gate before any
Core, Supervisor or provider access. It persists attempt count, last/next
attempt, capped `1m`/`2m`/`5m`/`15m` backoff and evidence deadline, enforces
task-level single flight, bounded batches and timeouts, and never schedules
beyond the deadline. Expired work terminalizes idempotently with
`restart_verification_window_expired`. Reconciliation remains readback-only
and cannot redispatch.

Health identifies the exact active plan and task and reports bounded timing,
pending, avoided-probe, expiry, collision, terminalization and failure
evidence. Inactive health clears active identifiers.

## Exact upstream compatibility

Exact 7.14.2 retains 78 advertised tools, 26 admitted reads and 74 configured
tools. Exact 8.0.0 uses an independent reviewed release entry and complete
78-tool snapshot: 24 automatic reads, 14 mixed/wrapper-required, 32 persistent
writes, four physical/high-risk actions, one prohibited, one unsupported and
two held. The held tools are `ha_search` and `ha_get_operation_status`; they
are accounted but not registered or callable. Exact 8.0.0 therefore exposes
72 configured tools. Unknown later 8.x releases expose no delegated reads and
do not inherit 8.0.0 trust.

The existing operational admission fingerprint model remains authoritative
for runtime admission. Separately named strict full-contract fingerprints are
retained as broader evidence. Dashboard access remains canonical list/get by
`url_path`, with no fuzzy selection, `view_path`, screenshots, preferences or
mutation. Backup and lifecycle arguments and approval/task ownership remain
bounded. The Engineering protocol allowlist is unchanged and fallback remains
zero.

## Evidence boundaries

Static/source and exact-image discovery initialize and call `tools/list`
without Home Assistant credentials or tool execution. They do not establish
production architecture, the running add-on digest, live held-tool behavior,
dashboard runtime output/not-found behavior, Supervisor backup response or
progress, reload/restart identity or readiness, outage/recovery, connector
reconnection, real-outage no-blind-redispatch, or post-deployment stale-record
CPU behavior. Both held tools remain held after deployment; admission requires
a later reviewed change.

## Upgrade and rollback

After an independently approved merge and publication, an operator may update
only the Engineering Beta add-on through the normal Supervisor path and must
follow [`V2_2_0_BETA11_ACCEPTANCE.md`](V2_2_0_BETA11_ACCEPTANCE.md). No Home
Assistant Core restart or option mutation is expected. If acceptance fails,
stop write acceptance, retain evidence and use the separately authorized
normal Supervisor procedure to return to the previously accepted Engineering
image. Stable v1.1.2 is operationally retired and is not a supported rollback.

Source implementation and review perform no deployment or live Home Assistant
access.
