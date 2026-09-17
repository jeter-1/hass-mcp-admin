# Typed ordinary fan control (HAMCP-141)

`control_fan` is an additive Engineering tool. It uses ordinary authenticated
connector authority for one exact fan action; it does not create a configuration
plan or request panel approval. Existing governed operations retain their panel
approval requirements. Generic `call_service`, arbitrary services, bulk targets,
WebSocket commands and fallback remain unavailable.

The assistant generates and retains an operation ID once, in the form
`<10-digit UTC Unix seconds>-<32 lowercase UUID hex digits>`. The owner does not
manage IDs. New IDs are accepted within five minutes, with 30 seconds of future
clock tolerance. Reuse the same ID and exact arguments to reconcile, or retrieve
its task with `get_execution_task`. A repeated call never authorizes another
mutation, including after a timeout. A changed action requires a different ID;
uncertainty must be reconciled before considering a new action.

Supported actions are `turn_on`, `turn_off`, and `set_percentage`. Percentage is
a strict integer from 0 through 100. `turn_on` may include a percentage in its one
call; `set_percentage` requires it; `turn_off` forbids it. Zero requests OFF.
Unsupported fan features, unavailable/ambiguous targets, malformed arguments,
stale state and unavailable authority refuse. An already satisfied request is a
verified no-op with zero provider attempts.

Fan state/speed changes can cause motion and automation reactions. Consumer
coverage is incomplete; that disclosure does not itself prohibit an exact,
owner-requested action. Verification establishes Home Assistant's reported
state and exact requested percentage, not independent physical feedback.
One HA fan entity can represent integration-defined groups or consumers; this
wrapper does not independently enumerate their physical effects.
Integration quantization is not rounded into success. There is no automatic
restoration or rollback: reconcile first, then obtain a separate owner request
for an exact restoration action if needed.

## Provider and Core contract

Only ha-mcp 8.4.3 (`ha-mcp-v8.4.3-d5cea47a`, protocol 2025-03-26) and the
complete reviewed descriptor contract may serve this wrapper. The fixed
`ha_call_service` arguments contain `domain=fan`, one entity, the typed action,
only its percentage data, `wait=false`, `return_response=false`, and
`verbose=false`. The non-waited path avoids the upstream component fallback.
The public mixed-purpose upstream tool remains withheld.

The new binary-owned contract applies only to Core 2026.9.2. It additionally
requires current Core entity-state, service-discovery and F3-verification
capabilities. Core identity is checked inside lease acquisition. The existing
17 signed references do not grant fan authority: the compiled fan contract is a
separate decision. Signed registry disablement, expiry, revocation, integrity
refusal and generation retirement still withhold the required base authority.
No existing profile/probe fingerprint, signed entry or signing policy changes.
Other Core releases retain their existing capabilities and cannot use this fan
contract merely by matching older read profiles.

Source authority:

- Core `33c3e0cca60e73a8c4970ee677d75b8bc6464cdf`, fan implementation SHA256
  `cfe36916625e40c89b395d091de87ad4230f78b854a4b126e99e2aa287731a19`.
- ha-mcp `eac7a3aa7063432e9af17e7d7726040e909c7b8f`, service implementation
  SHA256 `3a8e95bc99287c999f71c3debc1dd1e5694b654de7c660db2c225a35c4efbe73`.

## Execution, receipts and recovery

The authenticated gateway binds the exact typed request in process and retires
that binding at request completion. Caller labels and request IDs are not
credentials. The operation uses shared F3 ownership, durable intent and locks
for the exact entity, Core and selected ha-mcp provider. It contends with
Engineering restarts of those dependencies. Ordinary execution records use a
separate `ordinary-fan-v1` namespace under the existing governance storage root;
previous plan/task/lock formats are unchanged. There is no invented plan ID or
panel-approval receipt.

One durable intent permits at most one mutating invocation. An acknowledgement
alone is insufficient; a lost or malformed acknowledgement leads to readback.
Provider attempt accounting and durable intent do not independently prove
remote delivery. The receipt preserves that distinction. Use task state and
verification, not the outer response's successful retrieval indicator, to decide
whether an action completed. Bounded responses retain reconciliation identities
and point to supported task retrieval.

Active owners take precedence over recovery. Owner loss before intent cancels
without dispatch; after intent, recovery reads only. A declaration visible before
its executor claims ownership remains pending until the original operation ID
expires; this prevents another process from cancelling still-authorized work. Verification has a
180-second evidence deadline and six observation/verification attempts. A late
or unresolved outcome becomes manual review and retains conflict protection only
for the exact unresolved fan. Core and ha-mcp availability dependencies remain
locked during active execution, but are released atomically when the target's
non-expiring hold is retained. A failed local settlement keeps the previous lock
set intact for fenced, read-only reconciliation; it never permits redispatch.
Task receipts expose retained keys and generations; health counts ordinary locks
and holds separately. Already settled holds do not consume the recovery budget;
there is no automated second action or lock override. External actors can still
change the fan between the final preread and dispatch: this is not an atomic
compare-and-set against Home Assistant or outside editors.

Provider exchanges have a 20-second outer bound and 15-second tool-call limit;
result envelopes are bounded at 60,000 bytes. New work has a 16-operation
capacity; receipt storage refuses new work at 4,096 records without evicting
identities into renewed authority. The recovery sweep is bounded to eight
records and 45 seconds. Ordinary receipts and holds are visible separately in
health. Runtime startup, process cancellation, and supported task reads use the
same read-only reconciliation path. Retained records are not automatically
expired or recycled by this implementation.

Authenticated request audit retains the selected provider, no-fallback attribution,
operation/task identities and available execution facts. Durable lifecycle events
and outcome snapshots are projected into the existing bounded audit logger,
including read-only background recovery after the request has ended. Recovery
preserves the initiating request correlation; it does not invent a new approval.
Durable intent and attempt count remain distinct from remote delivery, and
unresolved verification is unknown rather than false success. No raw provider
response or exception text enters these projections. Retained audit event IDs
deduplicate replay on task retrieval/reconstruction. Audit failures are reported
separately and do not change execution authority; supported receipt retrieval can
replay persisted events, subject to the existing audit-log retention window.

## Validation and rollout boundary

Offline tests exercise the actual gateway, typed handler, provider wrapper,
Core authority, shared F3 executor, durable stores and recovery using synthetic
inputs. The existing disposable Core 2026.9.2/ha-mcp 8.4.3 lane includes an
in-memory fan, three exact changes, duplicate receipts and independent OFF
readback. Adding that lane assertion is not evidence that its container run has
executed; candidate receipts report execution separately.

Registration grows from 51 to 52 static tools (77 with the unchanged 25 admitted
reads). Older descriptors stay compatible. Release preparation must update the
version and exact acceptance/catalog reference, obtain independent review, and
run candidate CI including the disposable lane. Deployment and household fan
verification require separate authorization. Source recovery is an ordinary
revert; it cannot undo a dispatched action. Preserve any ordinary records and
holds when planning deployment recovery.
