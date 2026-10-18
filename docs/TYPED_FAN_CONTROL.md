# Typed ordinary fan control (HAMCP-141)

`control_fan` is an additive Engineering tool. It uses ordinary authenticated
connector authority for one exact fan action; it does not create a configuration
plan or request panel approval. Existing governed operations retain their panel
approval requirements. Generic `call_service`, arbitrary services, bulk targets,
WebSocket commands and fallback remain unavailable.

The assistant generates and retains an operation ID once, in the form
`<10-digit UTC Unix seconds>-<32 lowercase UUID hex digits>`. The owner does not
manage IDs. New IDs are accepted within five minutes, with 30 seconds of future
clock tolerance. Once a declaration exists, reusing the same ID and exact
arguments is reconciliation-only: it reads the saved receipt and never authorizes
another mutation, including after a timeout. Failures after a declaration return
its `task_id` for `get_execution_task`. A failure before any declaration was
saved returns no task pointer, reports `stage: preparation_failed` and
`dispatch: no_dispatch`, keeps the operation ID, and records nothing to read back;
no rejection is persisted for that ID. A changed action requires a different ID;
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

Only ha-mcp 8.4.3 (`ha-mcp-v8.4.3-d5cea47a`) or exact 8.5.0
(`ha-mcp-v8.5.0-e1538bcd`), protocol 2025-03-26, and the corresponding
reviewed descriptor contract may serve this wrapper. Admission is scoped to the
three tools the wrapper names (`ha_list_services`, `ha_get_state`,
`ha_call_service`): each must appear exactly once and match every reviewed
contract component. Other advertised tools grant nothing and are never called;
release identity, protocol, holds, revocation and Core authority still apply.
Admission refusal is a non-retryable `provider_unavailable` outcome. The capability
catalog describes the compiled family; each receipt identifies the selected
exact contract. Historical receipts keep their original contract and hash. The fixed
`ha_call_service` arguments contain `domain=fan`, one entity, the typed action,
only its percentage data, `wait=false`, `return_response=false`, and
`verbose=false`. The non-waited path avoids the upstream component fallback.
The public mixed-purpose upstream tool remains withheld.

The historical binary-owned contract applies only to Core 2026.9.2. It additionally
requires current Core entity-state, service-discovery and F3-verification
capabilities. Core identity is checked inside lease acquisition. The existing
17 signed references do not grant fan authority: the compiled fan contract is a
separate decision. Signed registry disablement, expiry, revocation, integrity
refusal and generation retirement still withhold the required base authority.
No existing profile/probe fingerprint, signed entry or signing policy changes.
Other Core releases retain their existing capabilities and cannot use this fan
contract merely by matching older read profiles.

Future compatible Core releases can instead select the explicitly compiled
`core.typed_fan_operation` profile through reviewed signed Core data. This uses
stable provider contracts `ha-mcp-<reviewed-version>-single-fan-v2` and records the
actual Core version and semantic fingerprint separately. The original three base
capabilities remain mandatory. The signed power profile alone does not authorize
fan control. Exact provider admission, arguments, readback and F3 ownership remain
unchanged. See [Core applicability and storage compatibility](CORE_RELEASE_REGISTRY.md#ordinary-fan-and-power-applicability)
for the new-record, recovery and downgrade boundaries. Existing signed data is not
expanded by this implementation; real-release admission remains separate.

Source authority:

- Core `33c3e0cca60e73a8c4970ee677d75b8bc6464cdf`, fan implementation SHA256
  `cfe36916625e40c89b395d091de87ad4230f78b854a4b126e99e2aa287731a19`.
- ha-mcp `eac7a3aa7063432e9af17e7d7726040e909c7b8f`, service implementation
  SHA256 `3a8e95bc99287c999f71c3debc1dd1e5694b654de7c660db2c225a35c4efbe73`.

The 8.5.0 source is `311d6dc273fb4e9a5b8cde0de15f69472a64fe44`;
its separate compiled provider evidence and adoption limits are recorded in
[8.5.0 compatibility](HA_MCP_8_5_0_COMPATIBILITY.md). Existing 8.4.3 source
evidence above remains historical and does not identify the 8.5.0 binary.

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
expires; this prevents another process from cancelling still-authorized work.

Observation (HAMCP-192). After dispatch the request polls the fan's state for up
to about 10 seconds (reads at 0, 1, 2, 4, 6, 8 and 10 s) and verifies on the
first exact match. A fan that is still settling returns an `observing` receipt.
Its entity lease is 30 seconds, renewed every 10 seconds while owned, so the
30-second recovery sweep re-reads it within about a minute and then every sweep.
Verification keeps the 180-second evidence deadline. Failed or incomplete reads
keep the six-attempt bound. The deadline is never decided before reading: the
first sweep at or after the deadline performs one final read. A match verifies;
otherwise the task becomes manual review and retains conflict protection only
for the exact unresolved fan.

Receipts add derived hints and do not change any stored format.
`observation_status` reports why the task is observing, the evidence deadline,
the earliest next re-read (`next_reobservation_not_before`), the attempt count,
and the last-observed and verified timestamps. A `lock_conflict` receipt names
each blocking task in `blocking_tasks`: task ID, lock key, state,
`lease_status`, expected release time and how it releases. The list uses the
lock store's own acquisition predicate, so it names exactly the holders that
caused the refusal. A holder blocks when it is a conflict hold, when either
side is exclusive, or when its lease has expired: expiry alone never frees a
key, even between two shared holders, until task-aware recovery releases,
transfers or holds it. `lease_status` is `active` (expected release at the
lease expiry), `conflict_hold` (expected release `null`; a governed release is
required) or `expired_pending_recovery` (expected release `null`,
`lease_expired_at` gives the expiry, and the recovery sweep reconciles it). A new command
on a held fan is still refused; it is never queued and never supersedes the
holder.

Governed hold release (HAMCP-192). A manual-review hold is never released
automatically. It can be released at the add-on's Ingress page
`/typed/fan/<task_id>`. The page uses the same Ingress identity check as the
existing approval pages; the system-wide Home Assistant administrator-role
check for every approval page is deferred to HAMCP-205. The page shows the exact reviewed facts
behind a single-use CSRF token bound to the record generation, prepared hash and
hold generation. The page also reads the device once and shows the requested
state and the freshly read state side by side, field by field, with an explicit
verdict, before the release button; the token is bound to that readback, so a
state change after review refuses the release until it is reviewed again.
Release performs one fresh state readback and records it with
the decision in an additive sidecar (`hold-releases/<task_id>.json`). It then
clears only that hold and writes one `fan_hold_release_action` audit entry.
The audit entry is written exactly once: its event ID derives from a stable
`hold_release_id` over the authorized decision, the audit logger deduplicates
by that ID, and the sidecar's `audit_recorded` is set only after a confirmed
write. A crash or a failed audit write at any step is finished by recovery
without a second entry. With auditing disabled, the release completes with
`audit_recorded: false` and `audit_unrecorded_reason: audit_disabled` (a
failed write reports `audit_write_failed`); once auditing is enabled again,
recovery backfills the missing entry once and sets `audit_recorded`. The
"exactly once" guarantee covers the retained audit log (the live file and its
`.1` rotation). The
task outcome stays `manual_review_required`, and the readback match is reported
truthfully. Release never dispatches. Recovery may finish an interrupted release
from the sidecar without any provider access. An invalid sidecar fails closed
and keeps the hold.

Core and ha-mcp availability dependencies remain
locked during active execution, but are released atomically when the target's
non-expiring hold is retained. A failed local settlement keeps the previous lock
set intact for fenced, read-only reconciliation; it never permits redispatch.
Task receipts expose retained keys and generations; health counts ordinary locks
and holds separately. Already settled holds do not consume the recovery budget;
there is no automated second action, lock override or automatic hold release. External actors can still
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
