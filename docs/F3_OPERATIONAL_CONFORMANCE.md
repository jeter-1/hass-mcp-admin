# F3-C2 operational adapter conformance

## Status and boundary

F3-C2 adds runtime-inert implementations of `f3-operation-adapter-v1` for the
four existing operational-administration plan families. It is stacked on the
published F3-A Beta 16 executor and durable-lock API. The modules are not
imported by application startup, `ChangeGovernanceService`, current provider
routing, or public tool registration. F3-D remains the only activation point.

The adapters accept approved existing operational plans. They do not create a
second planning shape, consume approval, create execution tasks, or authorize
dispatch. The F3-A caller owns approval consumption, exact execution identity,
atomic lock acquisition, fencing, durable intent, duplicate execution,
cancellation, deadlines, terminal state, and process reconstruction.

No capability in this track provides rollback. Restore, deletion, download,
partial backup, retention changes, arbitrary reloads or service data, add-on
start/stop/install/uninstall/update/options/proxy actions, restart variants,
shutdown, host reboot, and fallback remain unreachable.

## Current Beta 15 operational inventory

| Operation | Plan and provider contract | Policy | Current dispatch and verification | Current recovery gap |
| --- | --- | --- | --- | --- |
| `create_full_backup` | `create_backup_plan`; target `backup:local_full_backup`; `upstream_operational_backup`; exact `ha_manage_backup` with `scope=snapshot`, `action=create`, and the planned name | standard administration, moderate risk delta, indirect physical consequence | applies through the current operational path; persists `before_dispatch`; reads operation state and inventory; requires a new exact-name backup outside the baseline with completed state, readable metadata, bounded creation time, and nonzero size when present | recovery is operation-specific and not yet governed by the shared F3 executor/locks; an ambiguous missing response must never create a second backup |
| `controlled_reload` | `create_reload_plan`; target one of `automation`, `script`, `input_boolean`, `input_number`; `upstream_operational_lifecycle`; exact `ha_reload_core` plural target | standard administration, moderate risk delta, indirect physical consequence | requires full configuration validation, exact service availability, and readable domain inventory; dispatches one reload; verifies connection, post-reload configuration, service, and inventory | a lost response has no authoritative direct effect signal; readback can prove readiness but cannot always prove the reload happened |
| `restart_addon` | `create_addon_restart_plan`; exact installed slug; `upstream_operational_lifecycle`; `ha_manage_addon` with only `action=restart` and exact slug | elevated administration, high risk delta, indirect physical consequence | binds one inventory/detail match, exact version/name/repository and upstream endpoint when applicable; verifies unchanged identity, running recovery, and evidence beyond an old running state | ordinary add-ons may lack a durable restart signal; the upstream add-on can use exact admission loss/restoration or process evidence |
| `restart_home_assistant` | `create_home_assistant_restart_plan`; target `home_assistant:core`; `upstream_operational_lifecycle`; exact `ha_restart(confirm=true)` | elevated administration, high risk delta, indirect physical consequence | requires valid configuration, exact HA/runtime/tool/storage/provider baseline; Beta 11 reconciliation persists immutable deadlines/backoff and uses a cheap eligibility gate before bounded expensive probes | existing reconciliation is specialized; F3 activation must preserve all of its durable evidence and must not reset the evidence window or redispatch |

All four plan families preserve immutable plan hashes, authority-v3 policy and
approval bundles, expiration, baseline evidence, public projections, warnings,
limitations, and the current provider descriptors. Planning remains read-only
and creates no task or approval challenge.

## Adapter architecture

`OperationalAdministrationAdapter` implements the shared lifecycle through
four explicit strategies:

- `FullBackupOperationStrategy`
- `ControlledReloadOperationStrategy`
- `AddonRestartOperationStrategy`
- `HomeAssistantRestartOperationStrategy`

Preparation copies only hash-bound plan facts into an immutable prepared
operation and rejects unknown operations before preflight. It binds the exact
capability, operation, target, policy snapshot, effect, provider descriptor,
arguments, verification contract, deadline class, warnings, limitations,
provider slug, and provider-identity evidence hash. Provider responses cannot
select an operation, target, or argument.

The internal capability identities are:

| Capability | Exact operation | Exact target class |
| --- | --- | --- |
| `create_full_home_assistant_backup` | `create_full_backup` | `local_full_backup` |
| `reload_home_assistant_configuration_domain` | `controlled_reload` | `home_assistant_configuration_domain` |
| `restart_installed_home_assistant_addon` | `restart_addon` | `installed_home_assistant_addon` |
| `restart_home_assistant_core` | `restart_home_assistant` | `home_assistant_core` |

Each capability binds the adapter contract, provider and provider operation,
closed argument surface, exact-release/provider contract, verification model,
readback recovery, no rollback, evidence-deadline class, and manual-review
policy. Unknown capability identities fail closed.

## Provider admission

The strategies reuse the existing backup and lifecycle gateways and compare a
fresh post-lock provider descriptor with the hash-bound plan descriptor. Exact
`ha-mcp` 7.14.2 and 8.0.0 with protocol `2025-03-26` remain the only current
admitted releases. Admission preserves full 78-tool normalized catalog
validation, `ha-mcp-reviewed-normalized-catalog-v1`,
`ha-mcp-operational-tool-descriptor-v2`, and zero fallback. Raw catalog hashes
remain diagnostics, not authority.

Exact 7.14.2 retains its reviewed bounded legacy lifecycle response. Exact
8.0.0 retains `ha-mcp-lifecycle-addon-structured-content-v1` inside
`mcp-direct-structured-content-v1`. F3-C2 neither accepts the structured model
for another release nor raises the generic text-result bound.

## Lock-set decisions

The complete resource/provider union is calculated before F3-A acquisition.
F3-A normalizes duplicate keys, unions evidence, applies exclusive dominance,
sorts bytewise, acquires atomically, persists generations, and releases in
reverse order.

| Operation | Complete F3-C2 lock set | Required conflicts and residual integration edges |
| --- | --- | --- |
| Backup | exclusive `backup:local_full_backup`; shared `home_assistant:core`; shared provider `addon:<exact-provider-slug>` | conflicts with another Engineering backup, provider restart, and HA restart. F3-D must add the sibling configuration/dashboard side when those operations require stable backup/HA state. External UI/client backups remain observable rather than lock-prevented. |
| Reload | exclusive `reload:<exact-domain>`; shared `home_assistant:core`; shared provider `addon:<exact-provider-slug>` | same-domain reload, HA restart, and provider restart conflict. Different domains may proceed concurrently because their exact services and readback are independent; F3-D must make configuration writes take the incompatible domain/core keys. |
| Add-on restart | exclusive `addon:<exact-slug>`; shared `home_assistant:core`; shared provider `addon:<exact-provider-slug>` | same add-on operation and HA restart conflict. When the target is the provider add-on, resource/provider evidence is unioned into one exclusive key. F3-D must make every operation depending on that provider take its provider key. |
| HA restart | exclusive `home_assistant:core`; shared provider `addon:<exact-provider-slug>` | conflicts with every operation requiring stable HA availability. F3-D must wire configuration, Dashboard write, dependency refresh, and other sibling adapters to the core lock; F3-C2 does not edit those tracks. |

Locks are exclusion evidence only. They never authorize dispatch. Preflight
validates that the exact acquired set matches the calculated set; F3-A validates
ownership and fencing immediately before the atomic intent transaction.

### Manual-review holds

The operation declarations identify only the evidence-sensitive target lock:

- backup: `backup:local_full_backup`, at most 24 hours;
- reload: exact `reload:<domain>`, at most 15 minutes;
- add-on restart: exact `addon:<slug>`, at most 30 minutes;
- HA restart: `home_assistant:core`, at most 30 minutes.

Unrelated provider/dependency locks should release at the transition. The
accepted F3-A Beta 16 API can currently promote only the complete acquired
handle and does not implement a bounded selective hold. F3-C2 does not fork or
patch that API. Therefore activation is blocked until F3-D provides an accepted
selective hold/release operation with bounded expiry, then binds these declared
sets and transitions. Current F3-C2 tests explicitly demonstrate this gap.

## Preflight

After caller-owned approval consumption and F3-A lock acquisition, shared
preflight rereads exact plan/task/operation/target/policy identity, active-task
identity, approval and elevated acknowledgement, plan expiry, task storage,
conflicting execution, and the complete lock set. HA restart additionally
requires healthy governance and audit storage.

Each strategy then performs the current authoritative planning reads again:

- backup rereads exact admission, idle operation state, readable inventory,
  and unchanged baseline identifiers;
- reload reruns full configuration validation and rereads the exact service
  and domain inventory;
- add-on restart requires exactly one complete installed match and rereads
  exact slug/name/version/repository/endpoint identity plus running state;
- HA restart rereads explicit configuration validity, HA and Engineering
  identity, tool accounting, all persistent storage health, exact admission,
  dependency recovery expectations, and zero fallback.

No preread updates the plan. Stale, malformed, ambiguous, unavailable,
unhealthy, or unknown-release evidence rejects before intent and invokes the
provider zero times.

## Dispatch, observation, and verification

F3-A atomically commits task/plan/operation/target/capability/attempt/request,
provider operation and argument hash, lock keys and fencing generations,
baseline fingerprint, UTC intent time, immutable evidence deadline,
`possibly_dispatched=true`, and `dispatch_count=1` before it calls the adapter's
provider boundary. Intent failure makes zero provider calls. Each attempt has a
maximum of one adapter dispatch, one provider mutation, and one simulated
effect. No strategy contains a provider retry loop.

Once intent exists, timeout, disconnect, crash, malformed response, or response
loss is possibly dispatched. Recovery loads the same lineage and calls only
observation and verification. It never invokes dispatch, resets a deadline, or
creates another attempt.

Backup requires a new identifier outside the approved baseline, completed
operation, readable exact-name metadata, bounded creation time, and nonzero
size when reported. A missing backup remains observing until the deadline and
then requires manual review; archive integrity and recorder inclusion are not
claimed.

Reload requires provider completion evidence when available plus connected HA,
valid post-reload full configuration, exact service availability, readable
domain inventory, and stable identity. With a lost response, those reads may
establish readiness but cannot manufacture a dispatch signal; ambiguity moves
to manual review without another reload.

Add-on restart requires unchanged exact identity, restored running state, and
restart evidence beyond an old running state. Existing gateway evidence may be
a process identity change, provider acknowledgement, or exact upstream
readmission. Missing evidence before the deadline moves to manual review.

HA restart preserves Beta 11's outage/reconnect evidence, exact HA and
Engineering identity, tool catalog, governance/audit/task storage, exact
upstream readmission, dependency-index recovery, post-restart configuration,
immutable deadline, persisted backoff, cheap eligibility gate, and bounded
expensive probes. A reconstructed attempt cannot redispatch.

## Duplicate execution and cancellation

F3-A task/plan/operation/target identity is validated before the executor claim.
An active duplicate reports the existing task; a terminal duplicate returns
its result. Neither acquires a second lock set, creates a second task, extends a
deadline, or dispatches. Corrupt or unrelated identity fails closed.

Cancellation is permitted only before durable intent and yields
`cancelled_pre_dispatch` with safe lock release and zero dispatch. Once intent
exists, cancellation is rejected because it cannot erase a possible external
effect. Cancellation is not rollback.

## External concurrency and residual races

F3 locks exclude cooperating Engineering executions, not all writers.

- An external backup, inventory change, or already-active provider operation
  observed before intent rejects or stales preflight. After intent it is
  distinguished through baseline identifiers and bounded verification; an
  unresolved attribution requires manual review.
- External reload, configuration mutation, HA restart, or service removal
  observed before intent rejects. Post-intent drift becomes verification
  mismatch or manual review; verification does not prevent the external act.
- Supervisor UI restart/stop/start/update can change add-on state, version,
  repository, slug, or endpoint. Pre-intent drift rejects; post-intent identity
  drift fails verification and never triggers redispatch.
- UI/Supervisor HA restart, HA update, host restart, or simultaneous provider
  disruption can overlap an intended HA restart. Pre-intent identity/storage
  drift rejects. Post-intent outage and recovery evidence may remain
  unattributable and therefore requires manual review.

F3-C2 does not claim all-writer exclusion. Fresh rereads occur while locks are
held and as close as the shared F3-A boundary permits, so this conformance track
does not intentionally widen the current preread-to-dispatch race.

## Outcome and observability model

The adapter returns the frozen normalized outcomes and lets F3-A map them to
execution-task schema version 1. Provider unavailability, contract mismatch,
missing or ambiguous target, invalid response, confirmed rejection, response
loss, indeterminate dispatch, verification mismatch/deadline, lock conflict,
stale baseline, invalid configuration, storage failure, and manual review stay
distinct bounded categories.

Per-operation metrics use a closed counter vocabulary for preparation,
preflight, admission, locks, intent, dispatch, response, observation,
verification, duplicate prevention, cancellation, recovery, and fallback,
with operation-specific backup/reload/add-on/HA counters. Events accept only a
closed set of bounded classifications, identifiers, hashes, and counts. Raw
provider responses, inventories, URLs, exception strings, tokens, and metadata
are never emitted. Central health integration remains reserved for F3-D.

## Migration equivalence and activation requirements

Synthetic equivalence tests compare approved Beta 15 plan projections with the
prepared F3 operations. Target, operation, policy class, risk delta, physical
consequence, provider admission, baseline, exact provider operation and
arguments, verification contract, warnings, limitations, and no-rollback
declaration remain identical. Differences are limited to F3 capability
identity, complete durable locks, durable intent/fencing, bounded recovery, and
normalized terminal outcomes.

F3-D must, before activation:

1. supply the production durable operation-evidence ledger by extending the
   existing execution-task/event persistence without changing task schema 1;
2. add accepted selective, bounded conflict-hold promotion/release to F3-A and
   bind the declared operation-specific hold policy;
3. wire sibling configuration/dashboard/dependency conflict edges without
   weakening their own boundaries;
4. preserve current planning and public routing while replacing only the
   approved apply/reconciliation internals;
5. prove exact durable-lineage migration for active restart reconciliation,
   immutable Beta 11 deadlines/backoff, duplicate apply, and recovery; and
6. rerun exact-release, architecture, immutable add-on, disposable real-HA,
   packaging, security, Full, Evidence, and exact-head CI gates.

Until those requirements are accepted, the package remains runtime-inert and
the draft PR is not eligible to merge or activate.
