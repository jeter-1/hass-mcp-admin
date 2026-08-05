# F3 parallel-development plan

Status: Beta 17 release-train order after merged F3-A

## Baseline and rule of engagement

F3-A / Beta 16 is merged. F3-B / Beta 17 restacks directly on that accepted
mainline and must merge before F3-C1 / Beta 18 (PR #90), which in turn must
merge before F3-C2 / Beta 19 (PR #91). F3-D / Beta 20 owns later activation and
integration. Every branch preserves exact-release behavior and secure
dependencies and may not merge before its listed dependencies.

The contract documents and `ha_mcp_engineering.f3.contracts` are shared review
boundaries. Repository-root `f3_contracts` is a compatibility/test facade, not
another definition. Tracks propose contract changes through the integration
owner; they do not fork names, outcomes, dispatch semantics, or lock keys.

Paths beginning with `ha_mcp_engineering/` or `config.yaml` are relative to
`hass_mcp_engineering_beta/`. Abbreviated runtime paths beginning with
`governance/`, `providers/`, or `tools/`, plus named runtime modules such as
`application.py`, are relative to
`hass_mcp_engineering_beta/ha_mcp_engineering/`. Paths beginning with `tests/`,
`docs/`, `.github/`, or another explicit repository root are repository-relative.

F4 retains multi-operation graph execution and generalized compensation.

## Dependency graph

```text
F3-A / Beta 16 (merged)
        |
        v
F3-B / Beta 17 (canonical contracts and dashboard deferral)
        |
        v
F3-C1 / Beta 18 (PR #90)
        |
        v
F3-C2 / Beta 19 (PR #91)
        |
        v
F3-D / Beta 20 (activation and integration)
        |
        v
 final F3 integration and release
```

Branches may retain parallel development work, but the accepted merge order is
Beta 16, Beta 17, Beta 18, Beta 19, then Beta 20. A later branch cannot use its
own green result as evidence for an unmerged dependency.

## F3-A — adapter and lock core

- Branch: `feature/f3-adapter-lock-core`
- Worktree: `/home/josh/worktrees/hass-mcp-f3-adapter-lock-core`
- Depends on: accepted F3-0
- Merge prerequisite: none beyond F3-0

### Ownership

F3-A owns new shared executor and lock modules, the declaration contract when a
reviewed correction is required, and focused adapter/lock core tests. Suggested
new files are:

- `ha_mcp_engineering/operation_adapter_executor.py`
- `ha_mcp_engineering/governance/locks.py`
- `tests/test_f3_adapter_core.py`
- `tests/test_f3_lock_manager.py`

The single integration owner exclusively owns:

- `governance/service.py`
- `governance/task_models.py`
- `governance/task_storage.py`
- `governance/storage.py`

F3-A supplies a small reviewed integration patch for those files; the
integration owner applies and serializes it. No feature track edits those files
concurrently.

### Deliverables and gates

- canonical lifecycle execution without generic provider forwarding;
- normalized outcome mapping without persisted-state rename;
- complete deterministic multi-key acquisition;
- durable task/plan-bound leases, renewal, release, and stale recovery;
- Home Assistant and exact-add-on dependency conflict edges;
- durable dispatch callback before one mutating provider invocation;
- no-blind-redispatch after possible dispatch;
- bounded lock health and audit evidence;
- phase-specific process-loss tests.

F3-A stops if it requires task-schema migration without separate approval,
changes provider arguments, creates fallback, makes a reserved state reachable
without migration review, or cannot justify lease/renewal values against long
backup/restart operations.

## F3-B — canonical contracts and governed dashboard planning

- Branch: `feature/f3-dashboard-writes`
- Worktree: `/home/josh/worktrees/hass-mcp-f3-dashboard-writes`
- Depends on: merged F3-A / Beta 16
- Merge prerequisite: canonical packaging, dashboard risk-policy review, safe
  planning bounds/compiler proof, and formal execution deferral

### Ownership

F3-B retains isolated planning and verification files:

- `governance/dashboard_operations.py`
- `governance/dashboard_transform.py`
- `tests/test_f3_dashboard_transform.py`
- `tests/test_f3_dashboard_write.py`
- synthetic exact-release dashboard planning/readback fixtures

F3-B avoids changing these current read files unless an independently reviewed
shared extraction is unavoidable:

- `providers/upstream_dashboard.py`
- `providers/upstream_contracts.py`
- `tools/dashboard.py`
- `clients/mcp.py`

Central registration, capability metadata, health aggregation, compatibility
registry changes, and CI are integration-owner files.

### Deliverables and gates

- exact complete internal raw read and explicit storage-mode identity;
- `f3-dashboard-json-pointer-patch-v1` validation and bounded semantic diff;
- unknown-field preservation and exact result hash binding;
- complete dashboard/resource/provider lock identities and nonmutating F3-A
  conformance;
- stale-hash rejection before durable intent;
- formal rejection before durable intent because atomic compare/save or
  all-writer exclusion has not been proven;
- exact verification from supplied complete readback evidence;
- no initial rollback capability unless the separately reviewed retention gate
  is satisfied;
- negative reachability for creation, deletion, resources, preferences,
  screenshots, metadata, arbitrary Python, services, and fallback.

F3-B accepts no setter realization. Generated `python_transform` and
full-configuration replacement are rejected; Engineering locks and final
reread do not close the demonstrated lost-update window. It stops on unbounded
config/diff persistence, ambiguous selector behavior, unreviewed risk policy,
any public/runtime dispatch reachability, or any physical/direct-service
reachability.

## F3-C1 — configuration-adapter conformance

- Branch: `feature/f3-configuration-conformance`
- Worktree: `/home/josh/worktrees/hass-mcp-f3-configuration-conformance`
- Depends on: accepted F3-0; accepted F3-A before merge
- Merge prerequisite: contract-v1 persisted fixtures and contract-v2 behavior
  both pass

### Ownership

- new configuration adapter/conformance modules;
- `governance/resources.py`;
- configuration-specific portions of `governance/normalize.py`,
  `governance/validation.py`, and `governance/risk.py`;
- `tests/test_dev14_resource_adapters.py`;
- `tests/test_dev14_configuration_plans.py`;
- new configuration conformance tests.

F3-C1 avoids central service, models, task storage, registration, central health,
workflow, version, and release files.

### Deliverables and gates

- automation, script, input_boolean, and input_number conformance;
- unchanged exact REST/WebSocket calls and closed schemas;
- unchanged planning/stale and verification normalization;
- exact create/update identity and collision checks;
- one mutating invocation per prepared operation, ordered
  stop-on-first-failure, and zero fallback;
- explicit legacy/v2 rollback capability difference;
- contract-v1 stored-record compatibility.

F3-C1 stops on delete or arbitrary forwarding, broadened rollback, changed
operation ordering, schema migration, changed tool registration/accounting, or
loss of exact stale/readback behavior.

## F3-C2 — operational-adapter conformance

- Branch: `feature/f3-operational-conformance`
- Worktree: `/home/josh/worktrees/hass-mcp-f3-operational-conformance`
- Depends on: accepted F3-0; accepted F3-A before merge
- Merge prerequisite: exact 7.14.2 and 8.0.0 provider matrices pass

### Ownership

- `governance/operational.py`;
- `governance/operational_lifecycle.py`;
- `providers/operational_backup.py`;
- `providers/operational_lifecycle.py`;
- `tests/test_2_1a_operational_backup.py`;
- `tests/test_2_1a_beta2_operational_lifecycle.py`;
- narrow Beta 15 lifecycle-response regression cases.

F3-C2 avoids central service/task/reconciliation, public registration, health
aggregation, workflow, release registry/policy data, version, and release files
unless the integration owner serializes a reviewed change.

### Deliverables and gates

- backup, controlled reload, exact add-on restart, and Home Assistant restart
  conformance;
- exact provider tools/arguments, release/protocol/catalog admission, and
  authoritative add-on binding;
- preservation of Beta 15 lifecycle response-envelope models;
- one dispatch lineage and readback-only indeterminate handling;
- unchanged approval, outage/readiness, verification, and zero fallback.

F3-C2 stops on new operation/action arguments, identity broadening, unknown 8.x
trust, changed protocol, changed provider timeout without evidence, fallback,
or any exact-release regression.

## F3-D — recovery, observability, and acceptance

- Branch: `feature/f3-recovery-acceptance`
- Worktree: `/home/josh/worktrees/hass-mcp-f3-recovery-acceptance`
- Develops against: accepted F3-0/F3-A interfaces
- Merge prerequisite: F3-A, F3-B, F3-C1, and F3-C2 accepted

### Ownership

- new process-loss/fault-injection harnesses;
- lock conflict, duplicate apply, response loss, deadline, and stale-state
  tests;
- exact 7.14.2/8.0.0 F3 acceptance fixtures;
- bounded health/audit snapshot tests;
- final milestone evidence tooling and acceptance results.

The integration owner handles any required edits to:

- `application.py` reconciliation supervision;
- `governance/service.py` reconciliation and health sections;
- `health.py` and `observability.py` aggregation;
- central CI and publication workflows.

### Deliverables and gates

- process loss at every phase, including before/after durable intent;
- lock conflict, lease renewal failure, stale owner recovery, and deadlock
  prevention;
- no blind redispatch under lost responses or process restart;
- backup readback reconciliation and configuration recovery gap decisions;
- deterministic deadline/manual-review outcomes;
- bounded diagnostics and audit attribution;
- exact-release accounting and provider-planning lanes with zero mutating
  dispatch, fixture mutation, and fallback;
- dashboard planning and exact readback-verification lanes for exact 7.14.2 and
  8.0.0, each proving zero setter invocation, zero fixture mutation, and zero
  live/physical/fallback action; and
- full milestone harness retaining formal dashboard execution deferral unless
  the authoritative atomicity gate is independently resolved.

F3-D stops if recovery can dispatch, evidence is unbounded, manual review can be
silently cleared, storage corruption is hidden, expected dispatch/mutation
counts differ, or exact-release accounting regresses.

## High-conflict shared files

One integration owner reserves these files. Parallel tracks submit small
integration patches rather than editing them concurrently:

- `ha_mcp_engineering/application.py`
- `ha_mcp_engineering/capabilities.py`
- `ha_mcp_engineering/health.py`
- `ha_mcp_engineering/mcp_server.py`
- `ha_mcp_engineering/observability.py`
- `ha_mcp_engineering/governance/models.py`
- `ha_mcp_engineering/governance/runtime.py`
- `ha_mcp_engineering/governance/service.py`
- `ha_mcp_engineering/governance/storage.py`
- `ha_mcp_engineering/governance/task_models.py`
- `ha_mcp_engineering/governance/task_storage.py`
- `ha_mcp_engineering/providers/dispatch.py`
- `ha_mcp_engineering/providers/routing.py`
- `ha_mcp_engineering/tools/governance.py`
- `ha_mcp_engineering/tools/registry.py`
- `upstream_release_registry.json` and `upstream_tool_policy*.json`
- `tests/test_beta_v2.py`, `tests/test_governance.py`, and
  `tests/test_execution_tasks.py`
- `.github/workflows/ci.yml`
- `README.md`, `ARCHITECTURE.md`, `V2_BETA_ARCHITECTURE.md`
- `docs/CHANGE_GOVERNANCE.md`, roadmap, and F3 contract documents
- `config.yaml`, `version.py`, changelog, release notes, acceptance metadata,
  and repository metadata.

The integration owner also owns tool registration, health wiring, version
bump, release notes, CI fan-in, and final evidence. No feature track bumps the
release independently unless the operator assigns a release boundary.

## Merge sequence

1. F3-0 contract freeze (accepted).
2. F3-A / Beta 16 (merged).
3. F3-B / Beta 17 (this branch).
4. F3-C1 / Beta 18 (PR #90, blocked until Beta 17 merges).
5. F3-C2 / Beta 19 (PR #91, blocked until Beta 18 merges).
6. F3-D / Beta 20 activation and integration.

Each merge re-runs complete task/schema/tool-count and exact-release invariants.
No track may use a passing neighbor branch as evidence until that neighbor is
accepted on the common base.

## Integration stop conditions

Stop the F3 merge train on runtime contract drift, schema migration without
authority, newly reachable arbitrary/write/fallback paths, inconsistent shared
file edits, changed exact-release accounting, unstable locks, possible double
dispatch, provider mutation in planning-only fixtures, any dashboard setter or
fixture mutation, unbounded diagnostics,
stable-v1 changes, or unresolved Critical/High review findings.
