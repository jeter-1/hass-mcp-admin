# Beta automation change governance

## Beta 21 handoff lifecycle interpretation

Change handoffs read persisted plan state without changing it. Proposed,
awaiting-approval, approved, and applying plans remain pending. `applied` counts
as completed only when required verification is `passed`. Verification failure is
failed/blocked; rolled-back work is labeled rolled back rather than active
completion. Full proposed configuration, unbounded diffs, secrets, authentication,
and prior approval as reusable authority are excluded.

Version 2.0.0-beta.18 retains the beta-only approval boundary for controlled
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
The original 25 compatibility tools are untouched. The transitional
`upsert_automation` tool remains callable with its original schema and behavior;
it bypasses governance and should not be used by clients that require approval,
stale-state protection, verification, or rollback evidence.

## Lifecycle and status transitions

```text
draft -> validation_failed
      -> awaiting_approval -> approved -> applying -> applied
                                      |            -> verification_failed
                                      |            -> failed
                                      -> expired / superseded

applied or verification_failed
  -> rollback_pending -> separately approved -> rolled_back
                                          |     -> rollback_failed
                                          -> expired
```

Persisted statuses are `draft`, `validation_failed`, `awaiting_approval`,
`approved`, `applying`, `applied`, `verification_failed`, `failed`,
`rollback_pending`, `rolled_back`, `rollback_failed`, `expired`, and
`superseded`. A newer plan for the same automation supersedes an older pending
plan so two proposals cannot silently overwrite one another.

## Planning, normalization, and fingerprints

`create_change_plan` accepts only `create_automation` and `update_automation`.
It validates the target ID and basic automation structure, reads existing state
for updates, and performs no write. Dictionary ordering is normalized;
behaviorally significant list ordering is preserved. Empty optional conditions,
variables, and trace settings are treated consistently, while unknown fields are
retained. Structured diffs identify known top-level fields and summarize other
fields without dumping unbounded content.

The current-state fingerprint and proposed-config hash are SHA-256 hashes of
canonical JSON. They do not include the access secret. The approval-bound plan
hash also covers the plan ID and version, operation, target, expiry, risk,
current-state fingerprint, proposed content, and approval kind. A material plan
mutation changes the hash and invalidates approval.

An update whose normalized current and proposed configurations are equal returns
`no_change`, creates no approvable plan, and cannot cause a meaningless write.

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

## Approval and expiration

Approval is an explicit, separate MCP operation. The client must pass the exact
`plan_hash` returned by planning or rollback request. Approval records the safe
caller identity and timestamp and is single-use. It does not write Home
Assistant state. A plan defaults to a 60-minute expiry; clients may request 5 to
1,440 minutes. Expired plans cannot be approved or applied.

This separation gives the reviewer a stable diff and risk record before any
write, prevents a generic `confirm=true` from authorizing mutable content, and
supports an auditable handoff between planning and execution.

## Apply, verification, and concurrency

`apply_change_plan` rechecks expiry, approval use, approval hash, risk, and the
live current-state fingerprint. It then obtains a per-automation lock, captures
the pre-change snapshot, consumes approval, writes through Home Assistant's
automation configuration endpoint, and reads the stored automation back.

Verification requires target existence, normalized desired-versus-read-back
equivalence, a matching actual fingerprint, Home Assistant configuration
validation, and recorded duration and mismatch fields. A successful HTTP write
with failed verification produces `automation_verification_failed`, preserves
the snapshot, and makes update rollback available. It never reports a successful
governed change.

If desired state is already present after a completed apply, a duplicate request
returns `already_applied` without another write. Approval is consumed before the
write so an ambiguous upstream failure cannot be retried as an unrestricted
duplicate. Per-plan and per-target locks prevent concurrent duplicate writes.
Current-state fingerprints reject stale plans. On restart, an abandoned
`applying` record is marked failed and requires a new plan.

## Rollback

Rollback is available only for governed updates with a pre-change snapshot.
The first `rollback_change(plan_id)` call creates `rollback_pending` state and a
new plan hash. The client must approve that exact rollback hash with
`approve_change_plan`, then call `rollback_change` again with the hash.

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
`change_plan_validation_failed`, `change_plan_approved`,
`change_plan_expired`, `change_apply_started`, `change_apply_rejected`,
`change_apply_succeeded`, `change_apply_failed`,
`change_verification_failed`, `rollback_requested`, `rollback_approved`,
`rollback_started`, `rollback_succeeded`, and `rollback_failed`.

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
approval_hash_mismatch
approval_already_consumed
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
3. Call `approve_change_plan(plan_id, expected_plan_hash)` with the exact hash.
4. Call `apply_change_plan(plan_id, expected_plan_hash)`.
5. Confirm `applied`, verification `passed`, and matching request IDs.
6. Call `rollback_change(plan_id)` to request rollback and review its new hash.
7. Approve that hash, call `rollback_change` with it, and confirm `rolled_back`.

Clients should always present diff, risk reasons, expiry, and exact hash before
approval. Create a new plan after expiry, stale-state rejection, ambiguous apply
failure, or external target changes.

Successful governed apply and rollback now invalidate the process-local entity
dependency index so the next analysis rebuilds configuration evidence. This adds no
write and does not change the persisted governance-plan format.
