# External human approval authority

Version `2.0.0-beta.25` separates an authenticated MCP caller from the human
authority that approves governed Home Assistant changes. A caller may create a
plan and request review, but only an authenticated Home Assistant administrator
using the add-on's Ingress panel can approve or reject the exact plan. The MCP
access secret is never accepted by the approval listener.

This boundary does not prove that an automation will behave correctly. Apply
verification proves that Home Assistant stored the intended configuration,
returned the expected automation identity, and accepted its configuration.
Beta 25 adds no behavioral observation window, mobile notification, service-call
tool, or background monitor.

## Listener and authentication boundary

The MCP listener remains port `8100`. The approval application listens on
internal port `8110`, configured only as `ingress_port`; `8110` is absent from
the add-on `ports` map and is not a host-accessible endpoint. The add-on enables
`ingress: true` and `panel_admin: true`. It does not enable `auth_api`.

Home Assistant Ingress authenticates the browser session and restricts the
sidebar panel to administrators. The approval application additionally accepts
requests only from the documented Supervisor Ingress peer and requires a valid
Ingress path header. A documented, syntactically valid remote-user identifier is
recorded only after those checks. Otherwise the honest bounded principal is
`home_assistant_admin_ingress`. Arbitrary forwarding headers are not trusted.
Approval routes are never mounted on port `8100`.

The panel is server-rendered and requires no JavaScript. State changes use POST,
one-time CSRF nonces, bounded form bodies, strict form content type and a request
timeout. Responses are bounded and include `Cache-Control: no-store`,
`Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, and a
restrictive Content Security Policy compatible with Ingress. There are no
external scripts, stylesheets, fonts, or remote resources. The application does
not set framing headers that would break Ingress.

All plan-, MCP-, user-, and Home Assistant-originated content is bounded,
sanitized, and HTML-escaped. The panel never renders raw HTML or displays access
secrets, Supervisor tokens, cookies, CSRF material after submission, authenticated
URLs, full configuration, unbounded diffs, raw logs, or trace payloads.

## Approval request contract

`approve_change_plan(plan_id, expected_plan_hash, approval_note)` retains its
public Beta 24 input schema. In Beta 25 the operation requests review; it does
not grant approval. For an eligible exact plan hash it creates or returns one
active challenge and reports:

```json
{
  "status": "approval_pending",
  "plan_id": "opaque-plan-id",
  "approval_kind": "apply",
  "bound_plan_hash": "exact-plan-hash",
  "external_approval_required": true,
  "approval_channel": "home_assistant_ingress",
  "challenge_id": "opaque-non-secret-id",
  "requested_at": "RFC3339 timestamp",
  "challenge_expires_at": "RFC3339 timestamp",
  "authority_version": 2
}
```

The challenge ID locates a pending record; it is not a bearer approval secret.
It cannot approve through MCP or through a direct listener request. The optional
`approval_note` is untrusted request context and is neither an approval,
credential, nor approver identity.

Each challenge is cryptographically random and bound to plan ID, plan version,
exact plan hash, approval kind, target type and ID, operation, risk, request
time, and expiry. Its lifetime is at most 15 minutes and never outlives the plan.
Repeated requests for the same eligible plan/hash/kind return the same active
challenge without extending it. A replacement after expiry invalidates the old
challenge. Plan expiry, supersession, rejection, hash change, or incompatible
state also invalidates the challenge.

Challenge and external-approval state is atomically persisted under
`/data/governance/change_plans` and survives an add-on restart. A restart never
approves, consumes, or revives an approval.

## Human review and decision

The pending inbox and review page display a bounded, escaped plan title,
description, ID, exact hash, plan version, approval kind, operation, target,
risk, expiration, changed fields, before/after summaries, warnings, validation
result, current approval state and whether apply is currently allowed. Rollback
review also displays the original apply timestamp, post-apply fingerprint,
snapshot fingerprint, rollback target, and exact rollback-bound hash.

Approve and Reject are POST-only actions. The server re-reads the persisted plan,
checks the one-time CSRF nonce, recomputes the current plan hash, and revalidates
the full challenge binding before atomically recording a decision. Approval
records authority version `2`, channel `home_assistant_ingress`, the bounded
approver principal, challenge and plan identifiers, exact bound hash, approval
kind, decision time, challenge request and expiry, and
`principal_separation_enforced: true`.

Rejection is terminal. A rejected plan cannot be approved, applied, reopened, or
reused. Reconsideration requires a new plan. Rejected plans remain readable as
historical records but are not open work, pending authorization, or blockers in
handoffs.

## Apply and rollback enforcement

Apply requires every prior governance gate plus authority version `2`, the
external Ingress channel and principal, principal-separation enforcement, the
exact challenge-bound plan hash, approval kind `apply`, unexpired and unconsumed
approval, and a non-rejected plan. Without that record,
`apply_change_plan` returns `external_approval_required` before a Home Assistant
write, provider write dispatch, apply snapshot, approval consumption, or index
invalidation.

After external approval, stale-state protection, snapshot capture, single-use
consumption, Home Assistant write/readback, configuration validation,
verification, idempotent duplicate apply, audit, and dependency-index
invalidation operate as in Beta 24. Duplicate successful apply reports
`already_applied` without another write.

Rollback has separate authority. The first `rollback_change` requests rollback
and creates a new exact rollback plan hash. `approve_change_plan` requests an
external challenge of kind `rollback`. An apply approval cannot authorize it.
Rollback before the external decision returns `external_approval_required`
without a write. After external rollback approval, the original snapshot is
restored and verified and that approval is consumed once.

## State, audit, and health

Approval states are `required`, `external_pending`, `approved`, `rejected`,
`consumed`, `expired`, and `invalidated`. Plan status also includes terminal
`rejected`. Bounded plan results expose state, authority version, channel,
principal, exact binding, kind, and timestamps when applicable; they never
expose CSRF nonces, cookies, Ingress credentials, or raw request headers.

Audit events include `external_approval_requested`, optional bounded
`external_approval_viewed`, `external_approval_granted`,
`external_approval_rejected`, `external_approval_expired`,
`external_approval_invalidated`, and `external_approval_consumed`. Records may
contain bounded IDs, channel, principal, kind, result, reason, timestamp and
server version. They exclude full configuration/diff, request notes, CSRF data,
cookies, authentication material, secrets and authenticated URLs.

Governance health additively reports whether external approval and the Ingress
UI are configured, authority version, pending/granted/rejected/expired/
invalidated/consumed counts, and the last safe approval failure category. These
metrics are observability only and never authorize a change.

## Upgrade from Beta 24

Beta 24 caller-approved records use approval authority version `1` or omit the
field. Beta 25 never silently upgrades, rehashes, or transfers that authority.
Active pre-Beta-25 plans fail closed with `approval_authority_mismatch` or
`external_approval_required` and must be recreated. Terminal applied,
rolled-back, expired, superseded, failed and other historical records remain
readable. Beta 24 automation behavioral normalization version `2` is unchanged.

## Disposable real-HA contract test

The required `real-ha-contract-tests` CI job starts a disposable official Home
Assistant Core `2026.7.2` container, bootstraps temporary local credentials,
uses the application's REST/WebSocket clients, and removes all container,
configuration, and credential state in cleanup. The blocking image is pinned to
multi-architecture digest
`sha256:1476924357b46e80735c13e94232ba5c853cac052e9df4bb28d50fa56348097b`.

The stage verifies id-less automation create/update/readback, exact returned ID,
not-found conversion, configuration validation, the application's REST
`/api/states` inventory contract, Home Assistant's core WebSocket state command,
WebSocket entity and area registries, the service catalog, the System Log
command, and actual trace list/detail shape after a harmless event in the
disposable instance. The job waits for Core to report `RUNNING` and for every
required integration to finish setup before testing integration-owned commands;
its startup and execution are bounded, and it never contacts the deployed Home
Assistant environment. To
update the baseline, review the new Home Assistant release, resolve its immutable
GHCR manifest digest, update both version and digest together in `ci.yml`, run
all contracts, and document the compatibility decision.

The WebSocket client preserves Home Assistant's documented app proxy at
`ws://supervisor/core/websocket`. For a direct Core base URL it uses the native
`/api/websocket` endpoint. Both URL contracts are tested so disposable direct-Core
validation cannot change the deployed add-on route.

## Deployed acceptance procedure

Run this only after the user installs Beta 25. Implementation and CI must not
access the deployed environment.

1. Call `server_info`, `list_capabilities`, and `get_server_health`. Confirm
   `2.0.0-beta.25`, 38 registered/25 canonical/zero planned capabilities, HA
   connectivity, healthy governance storage, authority version 2, enabled
   external approval/Ingress UI, zero baseline pending challenges, captured
   provider counters, and no production access.
2. As a Home Assistant administrator, open the sidebar approval panel. Confirm
   it loads only through Ingress, is not host mapped, starts empty, exposes no
   MCP secret, and shows the expected security headers where observable.
3. Capture a dedicated test automation's exact configuration. Create a harmless
   description-only update that omits top-level `id` and leaves triggers,
   conditions, actions, mode, enabled state and physical behavior unchanged.
   Present the exact plan and hash to the human.
4. Call `approve_change_plan` twice with the exact hash. Confirm
   `approval_pending`, one stable challenge, external approval required, no plan
   approval, HA write or provider write dispatch. Attempt apply before the human
   decision; confirm `external_approval_required`, no write/snapshot/
   consumption/index invalidation, unchanged automation and unchanged provider
   failure counters.
5. The human reviews the exact plan, target, operation, risk, diff, expiry and
   approval kind in Ingress and presses Approve. Confirm `get_change_plan` shows
   authority version 2, Ingress channel, honest principal, exact bound hash,
   enforced separation and no secret approval material.
6. Apply the exact hash. Confirm success, consumed approval, correct returned
   automation ID, passed readback/config validation, matching desired/actual
   fingerprints, only the description changed, and one index invalidation.
   Repeat apply and confirm `already_applied` with no second write or replay.
7. Request rollback and capture its exact hash. Request approval and confirm a
   new rollback-kind challenge. Attempt rollback before human approval and
   confirm no write. The human approves the exact rollback in Ingress. Execute
   rollback; confirm exact restoration, correct ID, passed verification,
   single-use consumption, and post-rollback index invalidation.
8. Create a second harmless plan, request review, and have the human Reject it.
   Confirm terminal `rejected`, permanent apply refusal, no reopening, no write,
   historical handoff classification, no pending authorization, and bounded
   rejection audit. Leave no active test plan.
9. Only after explicit user permission, create another harmless pending
   challenge, record it, restart **only** the Beta add-on, reconnect Beta and
   confirm the same unexpired challenge remains external-pending without
   auto-approval or consumption. Reject it through Ingress. Never restart Home
   Assistant or production.
10. Generate a bounded system-status handoff. Confirm one effective dependency
    coverage row, no synthetic provider failure, external-pending as
    authorization-required, approved-unapplied as pending, rejected and
    rolled-back as historical, no challenge internals, structured/Markdown
    agreement, and no mutation.
11. Inspect bounded audit output for request, preapproval refusal, grant,
    consumption, apply, rollback, and rejection events. Confirm no full configs,
    CSRF, cookies, Ingress data, secrets, Supervisor token, or authenticated URL.
12. Reconcile final health: actual dispatched failures only; zero pending
    challenges/apply operations/rollback pending/awaiting approval; rejected
    plans historical; zero failed applies/audit write failures/prohibited
    fallbacks; healthy storage; no replay or unauthorized write.

Production v1.1.2 (`hass_mcp_admin`, port `8099`) is outside this procedure and
must not be accessed or modified.
