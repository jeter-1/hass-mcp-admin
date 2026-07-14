# Beta 26 release notes

Version: `2.0.0-beta.26`

Beta 26 is the final narrow corrective beta before RC1. It fixes governance
expiry lifecycle defects found during deployed Beta 25 acceptance without
adding a tool, schema, input enum, notification, assistant-native prompt, write
route, or runtime monitor.

## Corrected lifecycle contract

- Plan expiration is one terminal transition. Once a plan is `expired`, later
  `get_change_plan`, `list_change_plans`, health, Ingress, and handoff reads do
  not append another `change_plan_expired` event, alter `updated_at`, rewrite
  governance storage, or emit duplicate audit/structured-log records.
- External challenge expiry is resolved before public projection. A challenge
  whose `challenge_expires_at` has passed is reported as `expired`, is excluded
  from the Ingress inbox and health pending count, and is never actionable.
- One shared lifecycle resolver supplies plan reads, list reads, Ingress review,
  approval requests, apply, rollback, health, and handoff governance evidence.
- An otherwise eligible, unexpired plan may request a fresh challenge after the
  prior challenge expires. The replacement never outlives the plan; the old
  challenge and nonce remain unusable, and an active replacement remains
  request-idempotent without extending its expiry.
- Apply and rollback remain fail closed before Home Assistant reads or writes,
  provider write dispatch, snapshot creation, approval consumption, or
  dependency-index invalidation when authority is missing, expired, invalid,
  consumed, wrong-kind, or legacy.

All Beta 25 principal-separation rules remain: MCP cannot grant approval,
authority version 2 comes only from administrator Ingress, apply and rollback
need distinct exact-hash approvals, rejection is terminal, and legacy active
authority never migrates silently.

The catalog remains 38 registered tools, 25 canonical tools, and zero planned
capabilities. Public schema version remains 1 and every Beta 25 public input
schema and enum is unchanged. Production v1.1.2 (`hass_mcp_admin`, port `8099`)
is untouched. The Beta slug remains `hass_mcp_engineering_beta`, MCP remains on
`8100`, and the internal unmapped Ingress listener remains `8110`.

## Post-deployment acceptance

Run this only after the user installs Beta 26. Stop if any lifecycle value
changes unexpectedly; do not downgrade metadata in place.

1. Call `server_info`, `list_capabilities`, and `get_server_health`. Confirm
   `2.0.0-beta.26`, 38 registered tools, 25 canonical tools, zero planned
   capabilities, healthy governance storage, authority version 2, and no
   production access.
2. Select one already-expired plan and record its `updated_at`, total event
   count, and number of `change_plan_expired` events.
3. Call `get_server_health` repeatedly, then `list_change_plans` repeatedly,
   then `get_change_plan` repeatedly for that plan.
4. Confirm `updated_at`, total events, and expiration-event count are unchanged,
   and bounded audit output contains no duplicate expiration lifecycle record.
5. Inspect one expired external challenge. Confirm plan reads report it as
   non-actionable, health excludes it from `pending_challenge_count`, and the
   administrator Ingress inbox does not display it.
6. For an otherwise eligible unexpired harmless plan, call
   `approve_change_plan` and confirm one fresh challenge is returned, its expiry
   is bounded by the plan expiry, and the old challenge cannot be used.
7. Attempt apply before human Ingress approval. Confirm a safe external-approval
   refusal with zero Home Assistant writes, provider write dispatch, snapshot,
   approval consumption, or dependency-index invalidation.
8. With a dedicated test automation, complete one description-only external
   apply approval and verified apply. Request rollback, obtain a separate human
   rollback approval, and verify exact rollback restoration. Do not change
   triggers, conditions, actions, mode, enabled state, or intended physical
   behavior.
9. Confirm final health reports zero pending challenges, active apply
   operations, rollback-pending plans, failed applies, audit write failures, and
   prohibited fallbacks. Confirm production was neither accessed nor modified.

## Rollback strategy

Do not lower the installed add-on version or edit version metadata in place. If
deployed acceptance fails, stop testing and publish a later Beta that reverts or
corrects Beta 26. Leave production v1.1.2 unchanged.
