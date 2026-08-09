# F3 current-state execution inventory

Status: Historical F3-0 source inventory at `2.2.0-beta.15`, with Beta 17
disposition

Source boundary: `8d30c6499cafc24783a09a982c19aff55e0a2084`

This document records current behavior before F3 implementation. It is not a
runtime migration, a new operation, or authority to dispatch. Current behavior
remains governed by [Change governance](CHANGE_GOVERNANCE.md),
[Operational administration](OPERATIONAL_ADMINISTRATION.md),
[ADR-007](architecture/ADR-007-GOVERNED-OPERATIONAL-ADMINISTRATION.md), and
[ADR-008](architecture/ADR-008-DURABLE-EXECUTION-TASKS.md).

## Beta 17 disposition

F3-A is merged and provides the runtime-inert executor, durable persistence,
and fenced lock core. Beta 17 makes
`ha_mcp_engineering.f3.contracts` the sole shipped definition of
`f3-operation-adapter-v1`; repository-root `f3_contracts` is an object-identical
compatibility facade only.

F3-B retains the dashboard planning and exact-verification foundation but
formally defers execution. The reviewed interfaces do not provide atomic
compare-and-save, expected-hash enforcement at the authoritative save
boundary, or exclusion of all external dashboard writers. Engineering locks
coordinate Engineering operations only, and final reread does not repair the
lost-update race. No dashboard setter, public tool, persisted operation,
dispatch route, or rollback is accepted.

## Inventory method and boundaries

The inventory was derived from the Beta 15 source, tests, reviewed release
registry, deterministic `tools/list` captures, and exact reviewed `ha-mcp`
7.14.2 and 8.0.0 source. No live Home Assistant, Supervisor, connector, or
production data was used.

The F3-0 change adds no runtime consumer of the declaration-only adapter
contract. It does not change plan schema, task schema, public tools, provider
routing, tool accounting, policy, approval, locking, dispatch, recovery, or
fallback behavior.

At this historical boundary the contract declarations lived in
`f3_contracts/operation_adapter.py`, outside the Engineering add-on package.
Beta 17 supersedes that packaging arrangement with the canonical shipped
module described above.

## Configuration adapters

| Source and symbol | Current responsibility | Lock and dispatch | Verification and recovery | Tests | Gap / later owner |
|---|---|---|---|---|---|
| `governance/resources.py::ConfigurationResourceGateway` | Exact automation REST, script REST, and input-boolean/input-number WebSocket reads and writes; no delete or arbitrary forwarding | Full replacement through resource-specific fixed calls | Exact resource readback and reviewed normalization | `test_dev14_resource_adapters.py` | Conditional gateway is not an explicit adapter; F3-C1 |
| `governance/resources.py::validate_resource`, `validate_resource_create_identity` | Closed configuration validation and helper identity/collision checks | Pre-dispatch only | Fails closed on unsupported shapes and identities | `test_dev14_resource_adapters.py` | Retain resource-specific differences; F3-C1 |
| `governance/resources.py::resource_fingerprint`, `compare_resource_verification`, `structured_resource_diff` | Current-state binding, bounded diff, and intended-result comparison | Hashes are plan/preflight evidence, not locks | Resource-specific semantic comparison | `test_dev14_configuration_plans.py` | Normalize outcome vocabulary without changing hashes; F3-C1 |
| `governance/normalize.py` | Separate planning/stale and verification normalization | No dispatch | Preserves unknown automation fields for planning; verifies reviewed aliases | `test_governance.py`, `test_dev14_configuration_plans.py` | The planning/verification distinction must survive conformance; F3-C1 |
| `governance/service.py::create_configuration_plan` | Builds an ordered 1–8 operation contract for automation, script, input_boolean, and input_number | Planning performs no write | Binds current fingerprint, proposed hash, policy, risk, expected verification | `test_dev14_configuration_plans.py` | Public `helper` resolves to two internal resource types; F3-C1 |
| `governance/service.py::_apply_configuration_plan` | Stale preflight, approval consumption, ordered apply, readback, configuration check | Sorted process-local target locks; at most one invocation **per prepared operation** | One bounded readback; lost response never redispatches or continues later steps | `test_dev14_configuration_plans.py`, `test_execution_tasks.py` | Restart recovery does not resume readback-only verification; F3-D |

Current configuration target keys are tuples of resolved resource type and
exact target ID. Effective examples are `(automation, automation_id)`,
`(script, script_id)`, `(input_boolean, entity_id)`, and
`(input_number, entity_id)`. Duplicate targets in one plan are prohibited and
caller order is authoritative. Multi-operation graph semantics remain F4.

Contract-v2 configuration plans retain exact prewrite snapshots but declare
rollback unavailable. Historical contract-v1 automation update plans retain a
separately approved stale-safe rollback path; automation creates do not. F3
must represent this difference as capability evidence rather than silently
broadening rollback.

## Operational adapters

| Source and symbol | Current responsibility | Lock and dispatch | Verification and recovery | Tests | Gap / later owner |
|---|---|---|---|---|---|
| `providers/operational_backup.py::ReviewedOperationalBackupProvider` | Exact-release/provider admission and fixed `ha_manage_backup` snapshot/create call | `before_dispatch` completes before one provider call; timeout 1,860 seconds; fallback zero | Bounded provider result | `test_2_1a_operational_backup.py` | Shared contract conformance; F3-C2 |
| `governance/operational.py::BackupAdministrationGateway` | Independent backup inventory and planning/verification evidence | Global backup target | Verifies one new matching completed backup | `test_2_1a_operational_backup.py` | Background restart reconciliation lacks a backup readback reconciler; F3-D |
| `governance/service.py::_apply_operational_backup` | Approval, durable attempt, provider call, verification | Key `("operational_backup", "global")`; one call | Indeterminate responses move to readback/manual review, never redispatch | `test_execution_tasks.py` | Normalize lock key and recovery outcome; F3-A/F3-D |
| `providers/operational_lifecycle.py::ReviewedOperationalLifecycleProvider` | Exact reload, add-on restart, HA restart, add-on identity, exact release/catalog/argument admission | Fixed tools and arguments; callback before one provider call; fallback zero | Exact response-contract and identity handling | `test_2_1a_beta2_operational_lifecycle.py`, `test_beta15_live_lifecycle_addon_response.py` | Preserve Beta 15 response models; F3-C2 |
| `governance/operational_lifecycle.py::OperationalLifecycleGateway` | Planning evidence, dispatch, outage/readiness and exact upstream verification | Operation-specific dispatch methods | Reload readiness, add-on identity/readmission, HA restart outage/reconnect proof | `test_2_1a_beta2_operational_lifecycle.py` | Reload verification proves readiness, not a direct reload effect; F3-C2/F3-D |
| `governance/service.py::_apply_operational_lifecycle`, `_resume_lifecycle_verification` | Durable dispatch, readback-only continuation, deadline and manual-review outcomes | Keys currently include operation name and target | One invocation; restart recovery may observe but never redispatch | `test_beta11_restart_reconciliation.py` | Cross-operation conflicts are absent; F3-A/F3-D |

Current lifecycle arguments remain exact:

- reload: `ha_reload_core` with one allowlisted target;
- add-on restart: `ha_manage_addon` with exact slug and `action=restart`;
- Home Assistant restart: `ha_restart` with `confirm=true`.

Start, stop, install, uninstall, update, arbitrary Supervisor commands, and
fallback remain unreachable.

## Shared plans, tasks, dispatch, and recovery

| Source and symbol | Current responsibility | Current invariant | Demonstrated gap / owner |
|---|---|---|---|
| `governance/models.py::ChangePlan`, `ConfigurationOperation`, `OperationalPlanDetails` | Immutable approved intent plus operation-specific evidence | Existing plan and operation enums remain persisted authority | Do not rename or add dashboard operation in F3-0; integration owner |
| `governance/task_models.py::ExecutionTask`, `ExecutionTaskState` | Mutable execution materialization and append-only events | Task schema `1`; single-dispatch operations reject multiple attempts | Normalized F3 outcomes must map to, not replace, persisted states; F3-A |
| `governance/task_storage.py::ExecutionTaskRepository` | Atomic task event/materialization storage | `execution-tasks-v1`, `fsync`/replace, corruption quarantine, 90-day retention | Plan and task remain separate atomic files; F3-D validates reconciliation |
| `governance/storage.py::ChangePlanRepository` | Plan storage and interruption recovery | Operational attempted work becomes verification-required; no provider I/O during load | Configuration interruption terminalizes without readback; F3-D |
| `governance/service.py::_record`, `_project_plan_event_to_task` | Persist plan, project task event, emit audit | Plan evidence is saved before task projection and audit | Cross-file transaction is not atomic; plan evidence repairs tasks; F3-D |
| `clients/upstream_read.py::McpReadGatewayTransport.execute_read` and operational providers | Exact dispatch callback boundary | Durable callback runs after admission and before provider invocation | Crash after callback is conservatively post-dispatch; F3-A freezes this boundary |
| `governance/service.py::reconcile_execution_tasks`, `reconcile_operational_plans` | Bounded readback-only recovery | No dispatch; max 20 records/pass, 10-second budget, lifecycle-only operational pass | Backup reconciliation and phase-complete fault injection remain gaps; F3-D |

Operational reconciliation starts once and then runs every 30 seconds. Restart
backoff is 60, 120, 300, then 900 seconds. A first dispatch creates a fixed
24-hour post-dispatch deadline. These are current runtime values, not new F3-0
defaults.

## Current lock implementation

`ChangeGovernanceService` owns two process-local dictionaries of
`asyncio.Lock`: one by plan ID and one by target key. Plan locks wait and then
resolve duplicate work idempotently. A locked target is rejected immediately
with `change_in_progress`; the effective target acquisition timeout is zero.
Multi-target configuration locks are acquired in deterministic sorted order
and released in reverse acquisition/context-exit order.

The current implementation has no durable lock manager, persisted lease,
lease duration, renewal interval, owner/task binding, stale-lock recovery,
provider dependency lock, cross-process coordination, or lock-specific health
counters. Process loss discards every lock. Current target equality also does
not make a Home Assistant restart conflict with configuration writes/reloads,
or an add-on restart conflict with operations depending on that add-on.

`waiting_for_lock` is a reserved but unreachable task state. F3-0 does not make
it reachable.

## Current outcome vocabulary

Plan fields, task states, provider categories, and operation-specific final
outcomes currently use overlapping names such as `not_applied`, `dispatching`,
`indeterminate`, `verification_pending`, `applied_verified`, `failed`, and
`manual_review_required`. The F3 normalized vocabulary is defined in
[ADR-013](architecture/ADR-013-F3-OPERATION-ADAPTER-AND-LOCK-CONTRACT.md).
It is an adapter/executor projection only; persisted strings are unchanged.

## Dashboard reads and reviewed write evidence

Current update (2026-08-08): the Beta 17 inert-foundation description below is
historical. The approved MVP now adds one governed F3 update operation for an
existing storage-mode dashboard through the exact 8.1.1 provider contract. The
two normal public read tools remain unchanged; the upstream setter is not
publicly or dynamically registered. See the
[current dashboard-write contract](F3_DASHBOARD_WRITE_CONTRACT.md).

| Source and symbol | Current responsibility | Current protection | Gap / F3 owner |
|---|---|---|---|
| `tools/dashboard.py::list_dashboards`, `get_dashboard_config` | Two public read-only dashboard tools | No setter registration | Preserve unchanged; F3-B adds inert planning and verification only |
| `providers/upstream_dashboard.py::UpstreamDashboardProvider` | Exact constrained `ha_config_get_dashboard` list/get | Canonical path, no screenshot/preferences, exact v2/v3 attestation | Public output may be sanitized/truncated while hashes cover raw config; not safe as write input |
| `providers/upstream_contracts.py` | 7.14.2 Dashboard v2 and 8.0.0 Dashboard v3 admission | Protocol 2025-03-26 and exact reviewed descriptors | Write admission needs its own exact constrained contract; F3-B |
| `clients/mcp.py::execute_dashboard_read` | Bounded MCP transport and exact read arguments | Full catalog/descriptor admission before read | No write route; retain |

The current read provider verifies the upstream 16-hex configuration hash and
also calculates a full Engineering SHA-256 evidence hash. It may sanitize or
truncate configuration before returning it. A later write planner therefore
requires a new internal complete raw projection that fails closed on
sanitization, truncation, or its reviewed size bound.

Exact reviewed `ha-mcp` sources:

| Release | Source commit | Setter input contract |
|---|---|---|
| 7.14.2 | `904c14ebbe76de700f7c3535f5cc71c017dca12e` | `ha_config_set_dashboard`, persistent write |
| 8.0.0 | `9dd3ac620e3149cd34ec3c990b6ee81e778191f2` | Same setter schema and behavior |

Both captures contain 78 tools. The setter schema is closed, requires only
`url_path`, and also exposes `config`, `python_transform`, `config_hash`,
metadata fields, strict-BPS fields, screenshot, and view arguments. The two
exact schemas are canonically identical. The reviewed registry fingerprint for
the setter input schema is
`a7d11d72710f1c39937bfc864291f6d0936b2d4feb68dc4ff049eda3b91a3ac1`.

Important source facts:

- the tool is create-or-update, not update-only;
- full replacement can proceed after a failed pre-read and can create a
  missing dashboard;
- its hash check and save are not an atomic compare-and-swap;
- arbitrary `python_transform` is executable text and its upstream sandbox
  explicitly is not a security boundary;
- inventory can contain YAML dashboards despite the current wrapper summary;
- strict BPS may require an hourly read-receipt key that is stripped before
  tool-body dispatch; and
- neither metadata writes nor screenshots are required to update config.

With the current default 60,000 response limit, Beta 15 reserves 16,000 for the
read envelope; sanitized pretty-JSON config must fit the remainder and each
sanitized string is capped at 20,000 characters. Raw compact canonical UTF-8
size is recorded separately. The setter has no hard write-size cap; its full
replacement path only warns at 10,000 bytes. These are not safe durable
snapshot/rollback limits.

As reviewed source evidence, generated-transform success returns `success`, action, exact path,
`write_committed`, `post_write_verified`, optional new `config_hash`, and an
optional post-save-read warning. It also echoes `python_expression`. Beta 17
rejects generated transform as a setter realization; the planning foundation
does not persist or expose the expression. Current
upstream errors distinguish missing/conflicting hash, strict-BPS
acknowledgment, transform validation, and save failure. After durable intent,
transport timeout or invalid response remains indeterminate; a provider
response is never sufficient verification.

These facts are frozen into the separate
[dashboard-write contract](F3_DASHBOARD_WRITE_CONTRACT.md).

## Demonstrated F3 gaps

1. F3-A now supplies the shared adapter core, normalized outcomes, and Beta 17
   canonical shipped contract API; production adapters are not yet migrated.
2. F3-A now supplies durable task-bound fenced leases; current production
   operations still use their existing locks until later activation.
3. The cross-operation conflict graph is incomplete.
4. Configuration restart recovery does not resume readback-only verification.
5. Backup is absent from the background operational readback reconciler.
6. Plan and task persistence require reconciliation across separate files.
7. Lock ownership, conflicts, renewal, recovery, and audit evidence are not
   observable in bounded detail.
8. F3-B supplies an inert complete internal dashboard read and governed
   planning path, but execution is formally deferred by the external-writer
   atomicity blocker.
9. Dashboard risk review, declarative transformation, and safe artifact bounds
   are implemented; rollback and executable mutation remain unavailable.

F4 retains multi-operation graph scheduling and generalized compensation.

## Ownership map

- F3-A: merged adapter executor, lock manager, normalized outcomes, durable
  boundary.
- F3-B: canonical shipped contracts plus dashboard planning/verification and
  formal execution deferral; no constrained setter is accepted.
- F3-C1: configuration adapter conformance without behavior expansion.
- F3-C2: backup and lifecycle conformance preserving Beta 15 contracts.
- F3-D: process-loss recovery, bounded observability, and acceptance harness.
- Integration owner: central service, persisted models, registration, health,
  workflow, version, and release surfaces.

The detailed ownership and merge graph are in
[F3_PARALLEL_DEVELOPMENT_PLAN.md](F3_PARALLEL_DEVELOPMENT_PLAN.md).
