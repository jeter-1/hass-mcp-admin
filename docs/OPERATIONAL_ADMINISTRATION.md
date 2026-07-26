# Governed operational administration

Version: `2.1.0-beta.1`

Dev1 adds one public proposal tool, `create_backup_plan`, and reuses
`get_change_plan`, `list_change_plans`, `approve_change_plan`, and
`apply_change_plan`. Planning, approval, dispatch, and verification remain
separate lifecycle steps.

## Backup contract

The only reachable operation is an internally constructed call to the exact
reviewed `ha_manage_backup` contract:

```json
{"scope":"snapshot","action":"create","name":"<bounded Engineering value>"}
```

Both compiled `ha-mcp` 7.14.1 and 7.14.2 releases carry the same exact tool
contract. The provider requires the reviewed server identity, release,
protocol, complete catalog, and all five `ha_manage_backup` fingerprints.
Unknown releases or drift fail closed. The mixed upstream tool is not
reclassified or generically registered. Restore, delete, list, download,
retention, credentials, arbitrary selections, provider arguments, service
calls, and direct-Home-Assistant fallback are unreachable.

The direct Supervisor alternative was rejected because it would create a
second administrative authority and permission boundary. Reusing the existing
secret-bearing upstream endpoint keeps one exact reviewed provider contract.
Independent verification does not trust the provider response: Engineering
reads Home Assistant `backup/info` through its existing authenticated
WebSocket client.

The reviewed upstream snapshot-create implementation creates one local
configuration and add-on archive when supervised. It sets
`include_homeassistant=true`, `include_all_addons=true` for the local
Supervisor agent, and `include_database=false`. Dev1 therefore must not
describe this as a recorder-database backup or claim archive-content integrity.
The upstream implementation requires the existing Home Assistant default
backup password but Engineering neither accepts nor handles encryption
material.

## Lifecycle and exact-once boundary

`create_backup_plan` checks the exact provider, reads a bounded baseline
inventory, normalizes a safe name, records medium infrastructure risk,
persists a contract-v3 immutable plan, and performs no write.

An external Home Assistant administrator must approve the exact plan hash
through the existing Ingress authority. Immediately before dispatch,
`apply_change_plan` checks the hash, one-time approval, expiration, provider
contract, Home Assistant inventory, and global one-backup lock. A changed
baseline is stale and requires a new plan.

Dispatch evidence, approval consumption, request identity, and attempt number
are persisted together in one atomic lifecycle record before the provider
call. Exactly one dispatch is permitted. A definitive permission, rejection,
or operation-failure response is terminal.
When transport loss or timeout means dispatch may have occurred, the plan
becomes `verification_required`; later calls resume readback only and never
send another create request. Restart recovery applies the same rule.

Verification requires exactly one newly observed backup outside the approved
baseline, the exact requested name, a date in the bounded apply window,
`state=idle`, a completed last-action event, a matching identifier where
available, readable inventory, and a positive size when reported. The evidence
separates operation completion, inventory readback, and
`archive_integrity_validated=false`.

Backup deletion is destructive and out of scope, so
`rollback_available=false`. A global lock is intentional because Home
Assistant backup creation is not safely concurrent.

## Configuration validation foundation

The existing `check_config` tool remains read-only and retains its public
response. Internal configuration governance now uses a reusable strict,
bounded interpreter for the same structured `{result, errors}` evidence.
Malformed, incomplete, invalid, error-bearing, or unavailable responses fail
closed and untrusted text is sanitized.

Backup creation does not require configuration validation. Future reload and
restart plans must require a fresh successful check during planning and again
immediately before apply; Dev1 does not expose either action.

## Audit and health

Operational audit records contain bounded plan, risk, approval, provider,
dispatch, operation-ID, verification, outcome, fallback, and rollback fields.
They exclude tokens, passwords, endpoints, raw provider content, and unbounded
metadata.

`get_server_health.operational_administration` labels its sources:

- plan and outcome counts are persistent governance state;
- active applies are current process state;
- provider request, dispatch, success, failure, and indeterminate counts are
  cumulative process state.

It reports plan type, created and attempted counts, successful, failed,
indeterminate, and verification-failed outcomes, the current global lock, last
success and failure, provider state, zero fallback, and unavailable rollback.

## Troubleshooting

- `backup_provider_unavailable` before dispatch is potentially retryable after
  the provider recovers; the approval remains unconsumed.
- `stale_target_state` means provider evidence or inventory changed; create a
  new plan rather than forcing the old approval.
- `backup_dispatch_indeterminate` or `backup_verification_timeout` means
  readback must continue. Do not create or approve a replacement merely to
  retry.
- `backup_verification_failed` is terminal for that approval.
- No result authorizes restore, deletion, reload, restart, or fallback.
