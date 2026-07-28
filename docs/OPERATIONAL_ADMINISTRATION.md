# Governed operational administration

Version: `2.1.1-beta.2`

Beta 2 completes 2.1A with four public proposal tools:
`create_backup_plan`, `create_reload_plan`, `create_addon_restart_plan`, and
`create_home_assistant_restart_plan`. All four reuse
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

## Shared lifecycle and exact-once boundary

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

Call-time catalog validation is a pre-dispatch boundary. If the required tool
is removed, its reviewed input, annotation, output, or runtime contract
changes, the wider catalog drifts, or the upstream version becomes unreviewed,
the shared transport preserves the typed validator result. Public apply returns
bounded `backup_provider_unavailable` evidence with the precise failure
category, `failure_stage=pre_dispatch`,
`provider_dispatch_occurred=false`, `backup_creation_attempted=false`, and
zero fallback. Approval is not consumed. Restore exact provider evidence and
replan rather than treating the result as failed backup creation.

Provider health owns one failure count for the precise validation category.
Governance owns the plan lifecycle record, one audit event, and public error
mapping. The transport only preserves the typed boundary and does not count a
second generic provider failure.

Verification requires exactly one newly observed backup outside the approved
baseline, the exact requested name, a date in the bounded apply window,
`state=idle`, a completed last-action event, a matching identifier where
available, readable inventory, and a positive size when reported. The evidence
separates operation completion, inventory readback, and
`archive_integrity_validated=false`.

Backup deletion is destructive and out of scope, so
`rollback_available=false`. A global lock is intentional because Home
Assistant backup creation is not safely concurrent.

## Beta 2 reload and restart contracts

The exact reviewed upstream tools remain classified as mixed or high-risk and
are never registered generically. Engineering-owned wrappers construct only:

- `ha_reload_core(target=<one reviewed plural target>)`, with no `entry_id`;
- `ha_manage_addon(slug=<exact planned slug>, action="restart")`;
- `ha_restart(confirm=true)`.

Every wrapper requires exact server identity, reviewed 7.14.1 or 7.14.2
release, protocol, complete catalog, and per-tool input, description,
annotation, output, and runtime fingerprints. Start, stop, install, uninstall,
update, configuration mutation, proxy calls, arbitrary provider arguments,
`reload_all`, config-entry reload, and generic service calls remain
unreachable. There is no fallback.

`create_reload_plan` accepts only `automation`, `script`, `input_boolean`, or
`input_number`. Planning checks the exact service, reads the selected domain,
and runs the same strict full configuration validation used by `check_config`.
Apply repeats validation and service discovery immediately before dispatch.
Verification requires Home Assistant connectivity, valid configuration, the
exact service, and a readable domain state inventory. An empty domain is valid
when the inventory itself is readable.

`create_addon_restart_plan` binds approval to one installed add-on slug, name,
version, state, and exact provider contract. Apply rejects changed identity.
Verification requires the exact slug, name, unchanged version, running state,
and restart evidence beyond merely observing a running add-on.

The Engineering add-on resolves its own authoritative installed identity from
Supervisor's caller-relative `/addons/self/info` endpoint with the existing
injected add-on token. Supervisor may prefix an installed slug with its
repository identifier, so the MCP server ID, source add-on slug, display name,
entity ID, and prefix/suffix guesses are not self-identity evidence. Planning
strictly decodes bounded self metadata, then requires the requested slug and
the exact reviewed installed-add-on read to agree with that identity.
Unavailable, malformed, incomplete, or conflicting self evidence fails closed;
the possible self target cannot silently become an ordinary add-on.

New plans persist the requested and resolved slug, name, version, repository
identifier when present, identity source, authoritative-self decision, and
target class. Apply-time revalidation must reproduce that evidence. A proven
self-restart is verified after startup by a changed persisted
process-instance identity, exact add-on readback, restored runtime identity,
healthy governance storage, and available audit continuity. The reviewed
upstream add-on is recognized only by the exact `ha_mcp` slug and
`Home Assistant MCP Server` name and must regain exact identity, version,
protocol, catalog, and compatibility admission before success. Other add-ons
require exact provider completion evidence.

Verified add-on restarts expose one additive
`operational.verification.evidence.restart_proof` grade:

- `process_identity` requires a changed Engineering process instance plus the
  complete self-restart readback contract;
- `upstream_readmission` requires the exact upstream add-on to be running and
  the reviewed upstream identity, version, protocol, catalog, and gateway
  admission to be restored; and
- `provider_acknowledgement` means the exact other add-on is running with
  unchanged identity after the provider acknowledged the restart. This is
  weaker evidence and does not claim an independently observed process cycle.

Historical records without `restart_proof` remain readable. Verification
evidence is mutable lifecycle evidence and is excluded from the immutable plan
hash.

`create_home_assistant_restart_plan` captures Home Assistant identity,
Engineering build and tool counts, upstream identity and admission, governance
and audit storage, dependency-index state, and full validation. Apply repeats
validation before the one permitted `ha_restart(confirm=true)` dispatch.
Verification requires the durable dispatch record and observed expected
connection-loss evidence,
Home Assistant recovery and identity, the same Engineering build and catalog,
governance and audit persistence, exact upstream readmission and catalog,
dependency recovery state, post-restart valid configuration, and zero
fallback. Current connectivity alone never proves a restart.

## Durable reconciliation

Before any action call, immutable dispatch intent, request ID, attempt count,
and approval consumption are committed transactionally. Provider response loss
after that boundary becomes `verification_pending`; it does not reopen write
authority. Startup recovery immediately attempts bounded readback-only
verification, and the background supervisor retries plans that remain pending
every 30 seconds. Each pass is bounded by eligible-plan count and execution
time, isolates per-plan failures, and excludes unapproved, undispatched, and
terminal plans. Concurrent apply and reconciliation share per-plan and exact
operation-target locks.

Automatic startup verification is the normal Engineering self-restart path:
`get_change_plan` is sufficient to retrieve the automatically completed
result. A later `apply_change_plan` may request or resume readback when evidence
is still pending, but it is not required after a successful automatic
reconciliation. Neither startup, periodic, nor caller-requested reconciliation
can invoke an operational provider action or redispatch.

Success returns the persisted verified result. Pending evidence remains
pending. A deterministic post-dispatch mismatch becomes verification failed.
When neither failure nor successful dispatch can be established, the result
remains indeterminate and requires manual review. Rollback is unavailable for
all four operational actions.

## Persistence and downgrade behavior

Existing contract-v1 and contract-v2 configuration plans remain in the legacy
governance namespace. Contract-v3 operational plans are written
transactionally under `operational-administration-v3`, including a separate
owned quarantine location for actual corruption. Listing and lookup span both
namespaces deterministically and reject duplicate plan IDs.

Downgrading to exact 2.0.1 with retained `/data` leaves the operational
namespace byte-preserved. Version 2.0.1 neither displays nor processes those
records, but it does not quarantine, modify, or delete them; legacy
configuration plans remain available. Reinstalling 2.1 restores the original
operational records and resumes `verification_required` plans through
readback only.

Operational plans cannot be approved, applied, or recovered while 2.0.1 is
running. Do not manually move operational records into the legacy namespace or
recreate a pending operation during the downgrade. Re-upgrade to 2.1 to resume
recovery.

## Configuration validation foundation

The existing `check_config` tool remains read-only and retains its public
response. Internal configuration governance now uses a reusable strict,
bounded interpreter for the same structured `{result, errors}` evidence.
Malformed, incomplete, invalid, error-bearing, or unavailable responses fail
closed and untrusted text is sanitized.

Backup creation does not require configuration validation. Controlled reload
and Home Assistant restart require a fresh successful check during planning
and again immediately before apply. Evidence distinguishes `valid`, `invalid`,
`unavailable`, and `failed`; no immutable whole-config fingerprint is claimed.
Add-on restart intentionally does not require Home Assistant configuration
validation because it may be needed while Home Assistant configuration is
invalid or unrelated to the add-on problem. It still requires exact installed
add-on identity, reviewed provider availability, hash-bound external approval,
fresh target revalidation, and operation-specific restart verification.

## Audit and health

Operational audit records contain bounded plan, risk, approval, provider,
dispatch, operation-ID, verification, outcome, fallback, and rollback fields.
They exclude tokens, passwords, endpoints, raw provider content, and unbounded
metadata.

Proposal tools use audit `access=proposal` and
`operation_class=proposal`; they are not pure reads even though planning
performs no provider action. Approval and apply remain writes. This compatible
string classification does not change the audit record shape.

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
- `operational_validation_failed` means validation or exact service
  availability blocked dispatch; approval is not consumed.
- `operational_contract_mismatch` means reviewed provider evidence drifted
  before dispatch; refresh evidence and create a new plan.
- `addon_not_found` is a non-retryable expected domain outcome: the exact
  requested slug is not installed. No plan, approval, dispatch, or fallback
  occurred, and the last exact provider-health state remains available. Use the
  exact installed Supervisor slug; do not transform an MCP ID or entity ID.
- `self_addon_identity_unavailable` means Supervisor self metadata was
  unavailable, malformed, or conflicted with installed-add-on evidence.
  Planning failed closed before a plan or action; restore authoritative
  Supervisor access and retry with a fresh proposal.
- `operational_verification_pending` means the single dispatch is durable and
  only reconciliation may continue.
- `operational_verification_failed` is a post-dispatch readback failure and
  cannot authorize redispatch.
- No result authorizes restore, deletion, generic execution, or fallback.
- Do not reuse an unapproved plan that was created under a previous
  `other_addon` classification for the Engineering add-on. Install the
  correction and create a fresh plan.
