# F3-C2 operational-adapter conformance

## Accepted source and activation boundary

Beta 19 is based directly on merged Beta 18 main
`cca0d5e00d75398ec66bca0c9c2f568d11f7497e`. It adds runtime-inert
conformance for `create_full_backup`, `controlled_reload`, `restart_addon`,
and `restart_home_assistant`. Application startup, governance service routes,
provider routing, task recovery, restart reconciliation, tool registration,
and public planning/apply/cancel/rollback routes do not import or instantiate
the package.

The implementation consumes the sole shipped canonical contract package,
`ha_mcp_engineering.f3.contracts`. Operational code does not redefine core F3
targets, locks, capability descriptors, prepared-operation bases, results,
outcomes, or recovery context. The built-image closure has no dependency on
repository-root `f3_contracts`, `f3_dashboard`, or `tests`.

Legacy operational planning and execution remain authoritative until F3-D.
Beta 19 creates no coordinator, background worker, private reconciliation
surface, hold-release authority, rollback route, dashboard execution, public
tool, or issue #92 helper-state execution.

## Existing operational inventory

| Operation | Existing exact contract | Policy and consequence | Verification limitation |
| --- | --- | --- | --- |
| `create_full_backup` | `ha_manage_backup(scope=snapshot, action=create, name=<planned>)` through `upstream_operational_backup` | standard administration; moderate risk delta; indirect consequence | recorder is excluded; archive integrity, restore, download, deletion, and retention are not validated or exposed |
| `controlled_reload` | `ha_reload_core(target=<reviewed plural>)` for automation, script, input-boolean, or input-number only | standard administration; moderate risk delta; indirect consequence | valid readiness after a lost response cannot prove that reload occurred |
| `restart_addon` | `ha_manage_addon(slug=<planned installed slug>, action=restart)` through `upstream_operational_lifecycle` | elevated administration; high risk delta; indirect consequence | unchanged running state is not restart evidence |
| `restart_home_assistant` | `ha_restart(confirm=true)` through `upstream_operational_lifecycle` | elevated administration; high risk delta; indirect consequence | current reachability or a provider response alone is not restart evidence |

Preparation is a read-only projection of an exact operational plan contract 3.
It preserves plan ID/hash, policy-decision and approval-bundle hashes, target,
baseline, provider identity/contract, exact operation and argument hash, risk,
physical consequence, effects, warnings, limitations, verification contract,
and rollback unavailability. Missing, consumed, stale, incomplete, ambiguous,
or prohibited plan evidence requires a new plan; it is never filled from
mutable current state.

## Closed capabilities

One `OperationalAdministrationAdapter` delegates to four strategies:

| Strategy | Capability identity | Target class |
| --- | --- | --- |
| `FullBackupOperationStrategy` | `create_full_home_assistant_backup` | `local_full_backup` |
| `ControlledReloadOperationStrategy` | `reload_home_assistant_configuration_domain` | `home_assistant_configuration_domain` |
| `AddonRestartOperationStrategy` | `restart_installed_home_assistant_addon` | `installed_home_assistant_addon` |
| `HomeAssistantRestartOperationStrategy` | `restart_home_assistant_core` | `home_assistant_core` |

Unknown operations, capabilities, targets, providers, releases, protocols,
arguments, response models, and action variants fail closed. No dynamic import,
arbitrary forwarding, fallback, restore/delete/download/retention operation,
unsupported reload, add-on lifecycle variant, shutdown, or host restart is
reachable. All four capabilities declare rollback unsupported and unavailable;
future rollback is a separate governed F3 operation with its own approval.

## Public-task and authorization boundary

The prepared identity binds one existing public task to one future durable F3
child execution. Operational plans are single-operation today, but the two
identities remain distinct so F3-D can adopt the approved child model without
reinterpreting plan intent.

The accepted ordering is:

1. claim the durable child and atomically acquire its complete lock set;
2. run final authoritative operation-specific preflight;
3. invoke the caller-owned idempotent complete-authorization callback;
4. commit canonical durable F3 dispatch intent; and
5. invoke the one reviewed provider mutation.

Preparation requires a bound, available approval bundle and bound elevated
acknowledgement where applicable. Preflight validates their hashes and current
validity but has no `approval_consumed` or
`elevated_acknowledgement_consumed` requirement. A consumed approval cannot
prepare a new child. Reconstruction starts from the existing durable child and
prepared evidence rather than reparsing consumed public authority.

The merged executor owns approval consumption and intent. The adapter passes
its callback directly to the existing narrow gateway/provider final boundary.
After that callback succeeds, C2 performs no probe, inventory read, policy or
approval decision, target resolution, baseline check, operation-evidence
write, sleep, retry, or branching callback before network mutation. Lock,
expiry, policy, provider, target, baseline, configuration, approval-callback,
and intent failures therefore invoke the provider zero times. An
approval-success/intent-failure retry reuses the same idempotent authority.

## Provider admission

All strategies reuse the reviewed Beta 15 backup and lifecycle gateways. Final
preflight compares fresh evidence with the hash-bound plan descriptor. Only
exact `ha-mcp` 7.14.2 and 8.0.0 with protocol `2025-03-26`, complete normalized
78-tool catalog admission, the exact per-tool operational descriptor, and the
reviewed aggregate/runtime fingerprint models are admitted. Raw catalog hashes
remain diagnostic only. Unknown release/protocol/tool contracts fail closed.

Exact 7.14.2 keeps its bounded legacy add-on response contract. Exact 8.0.0
keeps `ha-mcp-lifecycle-addon-structured-content-v1` inside
`mcp-direct-structured-content-v1`, including the large live-equivalent detail.
Structured acceptance and generic text limits are not broadened.

## Complete lock graph

All requests are canonical `LockRequest` objects using `LockMode` and
`LockScope`. Duplicate keys union scopes/reasons, exclusive mode dominates,
and keys are bytewise ordered before merged F3-A atomically acquires and fences
the full set.

| Operation | Exact complete set |
| --- | --- |
| full backup | exclusive resource `backup:local_full_backup`; shared resource `home_assistant:core`; shared provider `addon:<authoritative-provider-slug>` |
| controlled reload | exclusive resource `reload:<domain>`; shared resource `home_assistant:core`; shared provider `addon:<authoritative-provider-slug>` |
| add-on restart | exclusive resource `addon:<target-slug>`; shared resource `home_assistant:core`; shared provider `addon:<authoritative-provider-slug>` |
| HA restart | exclusive resource `home_assistant:core`; shared provider `addon:<authoritative-provider-slug>` |

The reload key exactly matches Beta 18 configuration writes' shared
`reload:<domain>` key. Different reload domains and unrelated add-ons remain
compatible. HA restart conflicts with every cooperating shared-core operation.
When restarting the provider, its resource/provider scopes union into one
exclusive key. Locks exclude cooperating Engineering actors only; external UI,
Supervisor, host, update, and other-client races remain observable limitations.

## Selective non-expiring manual-review holds

Only the affected resource key is eligible for promotion:

- backup: `backup:local_full_backup`;
- reload: `reload:<domain>`;
- add-on restart: `addon:<target-slug>`; and
- HA restart: `home_assistant:core`.

Provider and core dependency keys release unless they are the same unioned
target key. The provisional 24-hour, 15-minute, 30-minute, and 30-minute values
are evidence-observation/administrative-escalation deadlines, never automatic
hold-release timers. Deadline expiry yields `manual_review_required`; a
promoted affected key stays held until verified resolution or explicit future
authenticated reconciliation. No adapter method can promote or release a
hold. The accepted shared core currently promotes a complete handle, so
selective promotion/release remains an explicit F3-D activation dependency.

## Authoritative operational evidence

Canonical F3 child records are authoritative. C2 removed the independent
operational recovery ledger and defines only a frozen, read-only bounded
`OperationalEvidenceProjection`. F3-D must map that view from the child record
and its operation evidence namespace. It cannot create another task or record.
JSONL and audit events remain secondary evidence and cannot reconstruct
authority.

The projection permits only bound IDs, intent time, immutable deadline,
dispatch count, response truth, optional provider IDs, outage/reconnect/
readmission predicates, bounded observation/verification counts, restart
backoff eligibility, manual-review reason, and selective hold keys. It excludes
raw responses, inventories, metadata, endpoints, configuration, credentials,
URLs, and exception text. Missing optional provider IDs never authorize retry;
corrupt or contradictory evidence fails closed to manual review.

## Operation-specific lifecycle

Backup preflight requires fresh exact admission, readable bounded baseline
identifiers, idle operation state, exact name, and unchanged inventory. It
verifies a completed new ID outside the approved baseline, exact name, creation
time bounded from authoritative intent time, readable metadata/post-inventory,
optional exact provider ID binding, and positive size when reported. A lost
response may verify from independent inventory; ambiguity never redispatches.

Reload preflight reruns full configuration validation and rereads the exact
service/domain inventory and baseline under the matching exclusive lock.
Provider acknowledgement plus valid post-readiness may verify. After a lost
response, unchanged connection, configuration, service, and inventory are only
readiness; without an independently reviewed reload-effect signal the child
stays observing and reaches manual review at its immutable deadline.

Add-on restart preflight binds exactly one installed slug/name/version/
repository/endpoint/target-class match in an acceptable running state plus
exact admission. Verification requires unchanged identity, recovered running
state, and reviewed effect evidence such as provider acknowledgement plus
recovery, process identity change, or exact provider readmission. An unchanged
running state after response loss is insufficient.

HA restart preflight requires valid full configuration, exact HA and
Engineering runtime/build/tool identities, healthy governance/audit/task/F3
execution/F3 lock storage, exact admission, dependency-recovery expectations,
compatible legacy restart reconciliation, the exclusive core lock, and zero
fallback. Verification preserves Beta 11's persisted outage/reconnect
predicate, immutable intent-relative deadline, durable bounded backoff, cheap
eligibility gate, bounded expensive probes, identity/storage/catalog/
admission/dependency/configuration recovery, and no redispatch. Provider
response or current reachability alone is insufficient.

## Recovery, duplicate, cancellation, and observability

Intent reserves `dispatch_count=1` before possible network I/O. Every timeout,
disconnect, crash, malformed/lost response, or crash immediately after intent
is possibly dispatched. Post-intent execution calls only canonical
`RecoveryContext`, the evidence projection, observation, and verification. It
never moves a deadline forward, invokes a strategy retry loop, or dispatches.

Merged executor identity/claim behavior owns active and terminal duplicates;
no C2 duplicate subsystem exists. Cancellation is accepted only pre-intent and
is rejected after possible dispatch. It is not rollback and cannot release a
manual-review hold. Canonical `NormalizedOperationOutcome` values map to
existing task-schema-1 states; C2 adds no persisted state.

Metrics and events use closed counters, bounded identifiers/hashes/categories,
and no provider content. Central health registration remains F3-D.

## Remaining F3-D dependencies

F3-D must supply durable public-task/child ownership, map operation evidence
into authoritative child records, activate one central startup/periodic
coordinator, add selective hold promotion and authenticated release through the
private Ingress reconciliation surface, implement separately governed rollback
Option A where approved, and migrate runtime routes. It must also prove sibling
lock edges, legacy restart reconciliation migration, and exact-head validation.
Issue #92 remains separate.
