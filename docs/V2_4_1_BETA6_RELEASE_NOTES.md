# Engineering 2.4.1-beta.6 release notes

Engineering 2.4.1-beta.6 repairs typed power and fan control (HAMCP-175) on
top of beta.5. After ha-mcp's three filesystem tools were enabled on
2026-10-12 (77 to 80 advertised tools), every `control_power` and
`control_fan` call was refused before dispatch. The response reported a
generic `internal_server_error` with `upstream_attempted=false`, and it pointed
at a task that `get_execution_task` could not find. Disabling the filesystem
tools restored power control live on 2026-10-13. This release removes the cause
so that unrelated catalog additions no longer block these writes.

**F1, dependency-scoped admission.** The typed power and fan provider now
admits a session on the exact tools it can call: `ha_list_services`,
`ha_get_state` and `ha_call_service`. Each must appear exactly once and match
every reviewed contract component (input schema, description, annotations,
output schema and title) and its policy classification. The server identity,
release, protocol, complete discovery, release entry and source commit,
revocation, HOLD, provider disposition, Core authority and same-session
revalidation checks are unchanged. Any other advertised tool grants nothing:
the provider refuses to call a name outside those three. The whole-catalog
validator is unchanged and still gates the generic read gateway, which keeps
its 25-tool subset.

**F2, truthful refusal.** A pre-dispatch admission refusal now survives the MCP
SDK's exception group. It surfaces as a bounded, non-retryable
`provider_unavailable` category instead of a transport error. Mixed or foreign
groups keep their transport semantics, and no exception text crosses the
boundary.

**F3, exchange telemetry.** Each provider exchange is counted and finished on
every path, so `upstream_attempted` is true whenever Engineering contacted
ha-mcp. The counts describe exchanges, not physical actions.

**F4, truthful task guidance.** A failure before the declaration is saved
returns no `task_id`. Instead it returns `stage: preparation_failed`,
`dispatch: no_dispatch` and `reconciliation: no_task`, keeps the
`operation_id`, and records the `preparation_failed_no_task` audit outcome.
Once a declaration exists, or its existence cannot be established, the pointer
and `get_execution_task` reconciliation guidance remain. That now holds when
the request is cancelled as well as when it fails: cancellation gets the same
durable-state audit cleanup and then propagates unchanged. A fresh, unsaved
operation ID can still execute once. A saved ID only reconciles and never
dispatches again.

No tool, public schema or description, routing, signed registry, HOLD, support
window, persisted format, workflow or stable-v1 change is included. The
healthy catalog remains **84 tools: 59 native and 25 delegated reads**. ha-mcp's
filesystem tools should stay disabled until HAMCP-177 ships, because dashboard
writes still apply whole-catalog admission and reject the expanded catalog.

Not included: the missing warning log for these refusals (HAMCP-172),
dashboard-write per-tool admission (HAMCP-177), and the live observations that
receipts report `dispatched_at=null` after a recorded dispatch intent and that
one live `turn_on` took about 38 seconds. The public `control_power` and
`control_fan` descriptions still say a repeated ID "only reconciles". The
response metadata and [power](TYPED_POWER_CONTROL.md) and
[fan](TYPED_FAN_CONTROL.md) documents qualify that rule to saved declarations.

Downgrade: beta.5 reads every beta.6 record, and nothing new is persisted. A
downgraded server returns to whole-catalog admission, which refuses power and
fan when an unreviewed tool is advertised.

Follow the [beta.6 acceptance contract](V2_4_1_BETA6_ACCEPTANCE.md).
