# HAMCP-149 Approval Completion Signal: Design Note (2.6.0 wave 1)

Status: design note only (HAMCP-332 wave 1). No runtime, test, schema, version or protected-path change. Selects no
implementation: the register requires the compatibility fixture first.
Base: `origin/release/2.6.0` = `7246f2d21693f039504542574569f03b71476489` (2.4.2-beta.7). Engineering citations are at
that base, relative to `hass_mcp_engineering_beta/ha_mcp_engineering/`. SDK citations are the pinned `mcp==1.30.0`
(`hass_mcp_engineering_beta/requirements.txt:1`), wheel sha256 `666edb50…84d0` matching `requirements.lock:59-60`,
upstream tag `v1.30.0` = `8c2fa6eabacde869d75568e33b9d466b58c2f512`, paths under `mcp/`. Spec citations are
`modelcontextprotocol/modelcontextprotocol` at commit `c4c367f9f58296a7053f5c78a52fd02bfbb56a49`, revision
`2025-11-25` (the SDK's `LATEST_PROTOCOL_VERSION`, `types.py:27`), per
[ADR-021](architecture/ADR-021-HOME-ASSISTANT-SOURCE-AUTHORITY.md).

## 1. Register outcome and binding decisions

HAMCP-149 Outcome (register, quoted): "An AI request bound to an exact plan, plan hash, and requesting client can
recognize final approval, rejection, or expiry without requiring Josh to type “done.” Active clients use bounded
authoritative-state checks; automatic continuation after a finished turn is offered only where negotiated client
support is proven." Blocker (quoted): "Actual ChatGPT connector support for completion notifications and automatic
conversation continuation is unproven. An initial approval may not complete a required elevated-risk acknowledgement."

Binding decisions:
- Josh, 2026-11-07 brief: HAMCP-149 is a 2.6.0 wave-1 feature; designs first.
- Register Next Action: run a non-executing client-compatibility fixture and "document negotiated client capabilities
  before selecting an implementation". This note therefore designs the fixture and a candidate signal only.
- Register Notes: a pane decision or completion signal "prompts an authoritative approval-state reread; it is not
  execution authority". Continuation only through the existing guarded apply path. Household approval reminders do
  not prove conversation resumption. Keep separate from HAMCP-133.
- HAMCP-222 audit ([§7.2](HAMCP_222_SIGNED_COVERAGE_AUDIT.md)): extending mobile push to completion, rejection or
  expiry widens a notify REST write that no signed profile names. The row does not authorize that; it is out of scope.

## 2. Current state at base

Approval stages and authoritative state (all Engineering-local):
- Three ordered actions: `plan_approval`, `elevated_risk_acknowledgement`, `safety_critical_acknowledgement`
  (`governance/models.py:81-88`). A granted first stage can move the bundle to `pending_elevated_risk_acknowledgement`
  or `pending_safety_critical_acknowledgement` (`governance/service.py:346-353`, `:10111-10128`); the pane then shows
  "Continue … acknowledgement" (`approval_web.py:1317-1324`). This is the Blocker's second-stage case.
- `approval_lifecycle`, `approval_actionable`, `next_required_operation` and `apply_allowed` are projected per read
  (`governance/service.py:3365-3393`, mapping at `:3493-3510`). After full approval `next_required_operation` is
  `None`; `apply_allowed` is the continuation fact.
- `get_change_plan(plan_id, detail_section, cursor, page_size)` (`tools/governance.py:499-527`) returns summary model
  `change-plan-observability-v1` with `status`, `plan_hash`, `approval_state`, `approval_lifecycle`,
  `approval_bundle_state`, `approval_actionable`, `apply_allowed`, `next_required_operation`
  (`governance/service.py:8059-8074`). It is persisted-only and calls no approval, lock or persistence method
  (`:8507-8521`), routes as `GOVERNANCE_PERSISTENCE` (`providers/routing.py:212`) and is Core-independent
  (`ha_core_readmission/routes.py:422-426`).
- Expiry: challenge TTL 60 min (`governance/service.py:277`), capped by plan expiry (`:10107-10110`). Reads project
  expiry without persisting (`:3301-3312`); the `external_approval_expired` event is persisted only on a later touch
  (`:9369-9379`). Any push keyed to persisted events would miss expiry at the instant it happens.
- Apply: `apply_change_plan(plan_id, expected_plan_hash)` (`tools/governance.py:706-714`, `governance/service.py:10660`)
  re-checks approval validity (`:15211`), stale state and at-most-once dispatch on its own.

Signals that exist today:
- `_record` persists the event, then calls the advisory notification observer (`governance/service.py:3136-3162`).
  The worker is "navigation hints only" (`governance/approval_notifications.py:1-5`). It sends on `*_requested` and
  clears on granted/rejected/expired/consumed (`:36-62`) via REST `notify` (`clients/paths.py:65`, service allowlist
  `:31`). It never reaches the requesting MCP client.
- Engineering sends no server-to-client MCP notification. Its only `send_notification` is the outbound upstream
  `initialized` (`mcp_sdk_compatibility.py:214-216`).

Requesting-client identity: plans store `requested_by=current_caller_id()` (`governance/service.py:4396` and 7 other
constructors), which is the first 12 hex of sha256(peer IP) (`routing.py:554-555`, `:641-644`). Behind the hosted
connector this identifies the relay peer, not a client or conversation. No MCP session id or `clientInfo` is bound.

Transport and SDK facts:
- Inbound MCP is stateless while the release registry is disabled (`mcp_server.py:33-38`), the default
  (`hass_mcp_engineering_beta/config.yaml:37`). Stateless mode makes a fresh transport per request with no session or
  event store (SDK `server/streamable_http_manager.py:209-228`). The session starts initialized with no client params
  (`server/session.py:94-98`), so `check_client_capability` is always false (`:132-135`) and `clientInfo` and client
  capabilities are not observable inside `tools/call`. Engineering does not narrow the inbound protocol set; the SDK
  echoes any supported requested version (`server/session.py:176-187`, `shared/version.py:3`).
- A notification without `related_request_id` goes to the session's GET stream or is dropped
  (`server/streamable_http.py:1067-1100`). Stateful mode allows one GET stream per session (`:743`). FastMCP is built
  without an event store (`mcp_server.py:27-38`), so a missed notification is never redelivered.
- Capability negotiation: the low-level server hard-codes `resources.subscribe=False` (`server/lowlevel/server.py:209-213`);
  `tasks` is advertised only if task handlers are registered (`server/lowlevel/experimental.py:70-85`); progress
  needs a client-supplied `_meta.progressToken` (`types.py:61-69`) and `Context.report_progress` is a no-op without
  one (`server/fastmcp/server.py:1197-1213`). Client capabilities (`types.py:417-433`) have no field for "resume an
  ended turn".
- Spec: progress tokens MUST belong to an active request and MUST stop after completion
  (`docs/specification/2025-11-25/basic/utilities/progress.mdx:60-62`, `:94`); resource updates need the `subscribe`
  capability (`server/resources.mdx:47`, `:223-232`); the GET stream is optional (`basic/transports.mdx:135-141`); after
  the response the server SHOULD end the request stream, and disconnection SHOULD NOT mean cancellation (`:124-128`).
  No spec text makes a host resume a finished model turn on a notification: that is host behaviour.

Missing: an outcome-level completion classification, any requesting-client binding stronger than an IP hash, and
any evidence of what ChatGPT or Claude connectors negotiate or do.

## 3. Proposed change

### 3.1 Phase 0: non-executing compatibility fixture (selects nothing)

Layer A, offline test fixture (`tests/`, synthetic, per `tests/AGENTS.md`):
- Binds `plan_id`, `plan_hash`, `requested_by` and an observed client record (protocol version, `clientInfo`, session
  id if any). The client record is observation, never authority.
- Uses the real governance service with a fake clock and a provider gateway and REST client that fail the test on any
  dispatch. Drives the persisted pane decision through `decide_external_approval` (`governance/service.py:9916`).
- After each step it calls `get_change_plan` and records the eight summary fields above. Scenarios: standard approve;
  elevated (stage 1 then stage 2); safety-critical; reject at stage 1 and at stage 2; challenge expiry (projected,
  then persisted on touch); plan expiry; invalidation by plan-hash change or replan.
- Transport rows use a loopback Streamable HTTP peer with the SDK client, stateless and stateful. They record the
  negotiated version and server capabilities, prove an unrelated notification is dropped (stateless, or no GET
  stream), and prove a mid-call disconnect leaves the plan unchanged.

Layer B, real-client observation (manual, needs Josh, §9 Q1): a disposable probe MCP server with a synthetic plan, no
Home Assistant, no Engineering store, no secrets, not the production connector. Per client and version it records:
`initialize` capabilities and `clientInfo`; whether `tools/call` carries `progressToken`; whether a GET stream or
`resources/subscribe` is attempted; the tool-call timeout; active-turn re-check behaviour; ended-turn behaviour when a
GET-stream notification arrives; and rejection, expiry, disconnect and missed-notification handling. Output: a dated
capability matrix. Only matrix rows marked proven may later claim automatic continuation.

### 3.2 Candidate minimal signal, safe regardless of client support

- **S0 (no runtime change).** `get_change_plan` is the completion check; the client re-reads within its turn.
- **S1 (additive, read-only; candidate, not selected).** An `approval_completion` block in the `get_change_plan`
  summary and the `approve_change_plan` pending response: `state` (`awaiting_decision`, `awaiting_second_action`,
  `approved_apply_ready`, `rejected`, `expired`, `invalidated`, `replan_required`, `consumed`, `prohibited`, `unknown`),
  `terminal`, `pending_action`, `plan_hash`, `challenge_expires_at`, `plan_expires_at`, a fixed `recheck_after_seconds`
  and `max_active_turn_checks`. It is derived only from the existing projection. `approved_apply_ready` requires
  `apply_allowed`. An unmapped value yields `unknown`, not terminal, with no continuation. It adds no persistence, no
  push and no call. The bounds must stay well under the inbound rate limit (120/min, burst 25,
  `hass_mcp_engineering_beta/config.yaml:47-48`).
- **S2 (deferred).** In-request progress during a bounded wait, only if Layer B proves `progressToken` support and
  long-call tolerance. Not proposed for build.
- Not proposed: `resources/subscribe` (needs an SDK-private override), tasks (changes tool execution), elicitation
  as approval (approval stays in the Ingress pane), mobile-push extension (HAMCP-222 §7.2), outbound client webhooks.

Authority rule for every option: signal, then authoritative re-read, then `apply_change_plan` with the exact hash.
The signal never grants, records or consumes approval and never calls apply. Routing stays Engineering-native,
Core-independent, with no `ha-mcp` and no Home Assistant call.

## 4. Public schema and tool-registration impact

Phase 0: none (tests only). S1: an additive output field on `get_change_plan` and `approve_change_plan`; no new tool,
tool count unchanged; the summary model id may need a version bump (build decision). Public output schema is a
security-sensitive surface, so S1 needs explicit scope and review. `governance/service.py` and `tools/` are protected.

## 5. Signing impact

None. Plan and approval state are Engineering-local. `get_change_plan` makes no Home Assistant call
(`ha_core_readmission/routes.py:422-426`). No Core command is added and the notify REST write is not extended. This
matches HAMCP-222 §3.9.

## 6. Negative reachability and refusal tests

- A stage-1 approval of an elevated or safety-critical plan never yields `approved_apply_ready`, and apply is refused.
- Rejection, expiry and invalidation are terminal with no continuation. Apply is refused.
- A plan-hash change after approval yields `invalidated` or `replan_required`. Apply with the old hash is refused.
- Reads (including expiry projection) leave stored plan bytes unchanged.
- No provider dispatch, no new `notify` REST call (call count equals the existing requested/clear baseline), and no
  server-to-client MCP notification in Phase 0 or S1.
- A second apply after continuation is refused or suppressed. An unknown lifecycle maps to `unknown`, fail closed.

## 7. Acceptance

- Success: in an active turn the requesting client sees `approved_apply_ready` within the check budget and applies
  once with the exact hash, verified. Both stages of an elevated plan are seen without Josh typing "done".
- Refusal: rejection, expiry and invalidation are recognized as terminal; nothing applies.
- Failure preservation: storage errors surface as today; a missed or dropped notification changes nothing because
  the re-read is authoritative; after a disconnect the re-read reports the true state.
- Recovery: after an ended turn, any next user message triggers the re-read. Automatic continuation is claimed only
  for Layer B rows marked proven.

## 8. Build slice and dependencies

1. PR-0: Layer A tests plus a capability-matrix template (tests and docs only).
2. Layer B: manual observation, separately authorized (§9 Q1).
3. PR-1: S1 only if Josh selects it after Phase 0; needs authorization naming the protected runtime paths.

No signing or Core dependency. The in-flight 2.5.0 PRs #384-#387 touch none of the files cited here. Rollback: PR-0
is test-only; S1 reverts as one additive field.

## 9. Open questions — pending Josh

- Q1. Authorize Layer B: who runs it, which clients, and a disposable reachable probe endpoint separate from production.
- Q2. After Phase 0, choose S0, S1 or S2, or ship only the fixture in 2.6.0.
- Q3. Requesting-client binding: accept plan id plus plan hash in the conversation, or persist a client identity
  (a persistence and schema change)?
- Q4. Typed-task release, F3 and record approvals (`approval_web.py:413-421`): in scope, or change plans only?
- Q5. Stateful transport independent of the registry flag is an architecture change. Consider it only if Layer B
  shows a client benefits.
- Q6. Completion push to mobile stays out of scope unless Josh names that write (HAMCP-222 §7.2).
