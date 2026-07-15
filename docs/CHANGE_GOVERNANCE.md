# Beta automation change governance

## RC1 governance freeze

Version `2.0.0-rc.1` preserves the accepted Beta 26 lifecycle byte-for-byte at
the public contract boundary. Persisted Beta 26 records load without migration,
terminal history is not rewritten, hashes and authority versions are preserved,
expired challenges are not actionable, and repeated reads remain idempotent.
Clean initialization creates no plans, challenges, or audit event. Authority
version 2 external approval remains the only executable trust path.

## Beta 22 handoff lifecycle interpretation

Change handoffs read persisted plan state without changing it. Proposed,
awaiting-approval, approved, and applying plans remain pending. `applied` counts
as completed only when required verification is `passed`. Verification failure is
failed/blocked; rolled-back work is labeled rolled back rather than active
completion. Expired, superseded, rolled-back, and terminal validation-only plans
are historical facts, not current pending work, blockers, or authorization needs.
Only active pending states can produce `change_pending`; only current unresolved
failures or requirements can block a handoff. Full proposed configuration,
unbounded diffs, secrets, authentication,
and prior approval as reusable authority are excluded.

Version 2.0.0-rc.1 requires external Home Assistant administrator approval for controlled
Home Assistant automation creation and updates. It does not alter production
v1.1.2 and does not govern scripts, scenes, dashboards, helpers, integrations,
devices, add-ons, system configuration, arbitrary direct service calls, or
automation deletion.

## Architecture

The six governance tools are registered directly with the beta FastMCP server:

- `create_change_plan`
- `get_change_plan`
- `list_change_plans`
- `approve_change_plan`
- `apply_change_plan`
- `rollback_change`

They use separate domain-model, normalization, risk, storage, and lifecycle
modules under `ha_mcp_engineering/governance`. Home Assistant access passes
through a narrow automation gateway, which tests replace with an isolated fake.
The original 25 compatibility tool schemas are unchanged. The transitional
`upsert_automation` tool remains registered for connector compatibility but
fails closed with `governance_required` before provider or Home Assistant work.
Automation writes use only the governed plan, approval, apply, verification,
and rollback lifecycle.

## Lifecycle and status transitions

```text
draft -> validation_failed
      -> awaiting_approval -> external_pending -> approved -> applying -> applied
                            |                  |            -> verification_failed
                            |                  |            -> failed
                            |                  -> rejected
                            -> expired / superseded

applied or verification_failed
  -> rollback_pending -> external_pending -> separately approved -> rolled_back
                                           |                    -> rollback_failed
                                           -> rejected / expired
```

Persisted statuses are `draft`, `validation_failed`, `awaiting_approval`,
`approved`, `applying`, `applied`, `verification_failed`, `failed`,
`rollback_pending`, `rolled_back`, `rollback_failed`, `expired`, and
`superseded`, plus terminal `rejected`. Approval states are `required`,
`external_pending`, `approved`, `rejected`, `consumed`, `expired`, and
`invalidated`. A newer plan for the same automation supersedes an older pending
plan so two proposals cannot silently overwrite one another.

## Planning, normalization, and fingerprints

`create_change_plan` accepts only `create_automation` and `update_automation`.
It validates the target ID and basic automation structure, reads existing state
for updates, and performs no write. Dictionary ordering is normalized;
behaviorally significant list ordering is preserved. Top-level automation `id`
is identity metadata and is removed before canonicalization, state fingerprints,
proposed-config hashes, plan hashes, and behavioral mismatch comparison. Identity
is checked separately against the requested target and any proposed or returned
ID. Empty optional conditions,
variables, and trace settings are treated consistently, while unknown fields are
retained. Structured diffs identify known top-level fields and summarize other
fields without dumping unbounded content.

The current-state fingerprint and proposed-config hash are SHA-256 hashes of
canonical JSON. They do not include the access secret. The approval-bound plan
hash also covers the plan ID and version, operation, target, expiry, risk,
current-state fingerprint, proposed content, approval kind, and approval
authority version. A material plan
mutation changes the hash and invalidates approval.

An update whose normalized current and proposed configurations are equal returns
`no_change`, creates no approvable plan, and cannot cause a meaningless write.

Beta 24 increments the normalization version. Hashes created with Beta 23 rules
are not silently rewritten or accepted. **Re-create any pending or approved
automation change plans after upgrading to Beta 24.** Terminal historical plans
remain readable; approval or apply of an incompatible record fails closed with
the existing hash-mismatch contract.

## Risk model

Risk classification is deterministic:

- **Low:** alias or description-only changes and other non-behavioral metadata.
- **Medium:** new automations, trigger/condition/time-window changes, mode or
  maximum-run changes, notification recipient behavior, HVAC/lighting changes,
  and non-critical physical actions.
- **High:** structured lock, garage-cover, alarm, valve/water-shutoff, host/core,
  broad-target, destructive, or immediate safety-sensitive actions.

Risk is based on actionable structure: action service/domain, target entity/device/area,
entity domain, trigger/condition type, and structured blueprint inputs. Alias,
description, event text, log messages, notification text, approval notes, caller context,
and non-actionable template literals cannot independently make a plan high risk.
Unresolved dynamic service or target construction is conservatively medium with a
warning and structured evidence. Evidence identifies the triggering field and category
without echoing complete target identifiers or secrets.

Low and medium plans require approval and may execute. High-risk plans remain
visible with deterministic reasons, but `approve_change_plan` and
`apply_change_plan` reject them with `high_risk_change_rejected`. Caller text or
an approval note cannot lower calculated risk.

Governed configuration reads, writes, verification, and rollback are
`direct_ha_required` facilitator capabilities. They do not route through ordinary
service execution or fall back to an unverified write. See ADR-002 for provider rules.

## External approval and expiration

Approval authority version 2 is external to MCP. The client must pass the exact
`plan_hash` returned by planning or rollback request to
`approve_change_plan`, but that call only creates or returns a 15-minute bounded
external review challenge and reports `approval_pending`. It never marks the
plan approved. Repeated requests are idempotent and do not extend an active
challenge. `approval_note` is untrusted request context, not human approval.

An authenticated Home Assistant administrator reviews the bounded escaped plan
through the admin-only Ingress panel on internal port `8110`. Approval or
rejection is POST-only, protected by a one-time CSRF nonce, and revalidates the
exact persisted plan/version/hash/kind/target/operation/risk. Approval records
the honest Ingress principal and principal-separation flag. It is single-use.
Rejection is terminal. A plan defaults to a 60-minute expiry; clients may
request 5 to 1,440 minutes. Neither a plan nor a challenge can be approved after
expiry.

Beta 26 resolves plan and external-challenge expiry through one lifecycle path.
`expired` is terminal: after the first transition, plan/list/health/Ingress/
handoff reads do not save the record, update `updated_at`, or duplicate events,
audit entries, or structured logs. Challenge expiry is reflected before public
projection, so dead challenges are not returned as actionable, are excluded
from health pending counts and the Ingress inbox, and fail closed in apply or
rollback. A still-eligible plan may request a fresh challenge bounded by its
own expiry; the replaced challenge remains unusable.

The MCP access secret does not authorize the approval listener, and approval
routes are absent from port `8100`. See
[`EXTERNAL_APPROVAL.md`](EXTERNAL_APPROVAL.md) for the complete boundary.

## Apply, verification, and concurrency

`apply_change_plan` rechecks expiry, approval authority version, external
channel/principal and separation flag, approval use, kind, hash, risk, and the
live current-state fingerprint. It then obtains a per-automation lock, captures
the pre-change snapshot, consumes approval, writes through Home Assistant's
automation configuration endpoint, and reads the stored automation back.

Verification requires target existence, an explicitly matching automation ID
when Home Assistant returns one, normalized desired-versus-read-back behavioral
equivalence, a matching actual fingerprint, Home Assistant configuration
validation, and recorded duration and mismatch fields. A successful HTTP write
with failed verification produces `automation_verification_failed`, preserves
the snapshot, and makes update rollback available. It never reports a successful
governed change.

Home Assistant commonly injects the correct top-level `id` into stored readback;
that does not create an `other:id` behavioral mismatch. A different ID produces
the explicit `automation_id` mismatch. If desired state is already present after a completed apply, a duplicate request
returns `already_applied` without another write. Approval is consumed before the
write so an ambiguous upstream failure cannot be retried as an unrestricted
duplicate. Per-plan and per-target locks prevent concurrent duplicate writes.
Current-state fingerprints reject stale plans. On restart, an abandoned
`applying` record is marked failed and requires a new plan.

## Rollback

Rollback is available only for governed updates with a pre-change snapshot.
The first `rollback_change(plan_id)` call creates `rollback_pending` state and a
new plan hash. The client requests review of that exact rollback hash with
`approve_change_plan`; a human separately approves kind `rollback` in Ingress;
then the client calls `rollback_change` again with the hash. Apply authority
never authorizes rollback.

Before restoring the exact snapshot, rollback verifies that live state still
matches the post-apply fingerprint. It writes, reads back, compares normalized
configuration, and runs configuration validation. External changes cause
`stale_target_state` instead of being overwritten. Rollback of a newly created
automation would require deletion, which is explicitly excluded; create
rollback returns `rollback_not_available`. No automatic rollback occurs.

## Persistence, retention, and recovery

Plans are stored only in beta add-on data, by default under
`/data/governance/change_plans`. Each uses a random 128-bit ID and an atomic
write-and-replace operation. Records survive restarts. Terminal records are
retained for 90 days by default. Invalid or corrupt records are quarantined and
counted in health output rather than preventing startup.

Proposals containing access-secret, token, authorization, cookie, password,
API-key, webhook-ID, or authenticated MCP URL fields are rejected before
persistence. Caller context is bounded to safe scalar metadata. IDs and hashes
never use the secret.

## Audit, health, and stable errors

Lifecycle events include `change_plan_created`,
`change_plan_validation_failed`,
`change_plan_expired`, `change_apply_started`, `change_apply_rejected`,
`change_apply_succeeded`, `change_apply_failed`,
`change_verification_failed`, `rollback_requested`, `rollback_started`,
`rollback_succeeded`, and `rollback_failed`, plus
`external_approval_requested`, optional `external_approval_viewed`,
`external_approval_granted`, `external_approval_rejected`,
`external_approval_expired`, `external_approval_invalidated`, and
`external_approval_consumed`.

Events contain only request ID, plan ID, target type/ID, operation, risk,
result status, stable error code, duration, caller ID, and approval state. The
gateway excludes proposed configs, caller context, approval notes, and hashes
from generic audit parameters. `get_server_health` returns bounded governance
counts and storage status without plan content.

Stable error codes are:

```text
change_plan_not_found
change_plan_expired
change_plan_not_approved
change_plan_rejected
approval_hash_mismatch
approval_already_consumed
external_approval_required
approval_authority_mismatch
external_approval_invalid
external_approval_expired
stale_target_state
change_in_progress
unsupported_change_operation
high_risk_change_rejected
automation_validation_failed
automation_apply_failed
automation_verification_failed
rollback_not_available
rollback_approval_required
rollback_failed
change_plan_storage_error
```

An absent record, including a lookup string that is not a generated plan ID,
returns `change_plan_not_found` and is non-retryable. It does not degrade storage
health. `change_plan_storage_error` is reserved for real read/write,
serialization, corruption, permission, or atomic-replacement failures.

For `create_automation`, a Home Assistant 404 during the ID-availability probe
is expected and does not set request failure telemetry. An existing ID returns
`configuration_conflict`; upstream 4xx/5xx or malformed success responses remain
real Home Assistant failures.

## MCP client example

These examples use generic IDs and no credentials or private entity names.

1. Call `create_change_plan`:

```json
{
  "title": "Adjust example notification",
  "description": "Change safe notification text",
  "operation": "update_automation",
  "automation_id": "example_notification",
  "proposed_config": {
    "alias": "Example notification",
    "trigger": [{"platform": "state", "entity_id": "binary_sensor.example_motion"}],
    "condition": [],
    "action": [{"service": "notify.example", "data": {"message": "Example activity detected"}}],
    "mode": "single"
  },
  "expiration_minutes": 60
}
```

2. Review the diff, risk, warnings, validation, expiry, fingerprints, and hash.
3. Call `approve_change_plan(plan_id, expected_plan_hash)` with the exact hash;
   confirm it reports `approval_pending`, not approved.
4. A Home Assistant administrator reviews and approves the exact plan through
   the Ingress panel.
5. Call `apply_change_plan(plan_id, expected_plan_hash)`.
6. Confirm `applied`, verification `passed`, and matching request IDs.
7. Call `rollback_change(plan_id)` to request rollback and review its new hash.
8. Request review of that hash, have the administrator approve the separate
   rollback in Ingress, call `rollback_change` with it, and confirm `rolled_back`.

Clients should always present diff, risk reasons, expiry, and exact hash before
external review. Create a new plan after rejection, expiry, stale-state rejection, ambiguous apply
failure, or external target changes.

## Beta 25 migration

New plans use approval authority version 2. Beta 24 pending or MCP-approved
records use legacy authority version 1 (including a missing field) and cannot be
applied. They are not silently upgraded or rehashed; recreate active plans.
Terminal historical applied, rolled-back, expired, superseded and failed records
remain readable. Automation behavioral normalization remains version 2.

Successful governed apply and rollback now invalidate the process-local entity
dependency index so the next analysis rebuilds configuration evidence. This adds no
write and does not change the persisted governance-plan format.
