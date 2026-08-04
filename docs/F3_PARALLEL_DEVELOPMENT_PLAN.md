# F3 parallel-development plan

Status: Frozen work-package boundaries after F3-0 acceptance

## Baseline and rule of engagement

Every F3 branch starts from the accepted F3-0 contract commit, preserves Beta
15 exact-release behavior and secure dependencies, and owns only the files in
its package below. A track may develop in parallel but may not merge before its
listed dependencies.

The contract documents and `f3_contracts/operation_adapter.py` are shared review
boundaries. Tracks propose contract changes back through the integration owner;
they do not silently fork names, outcomes, dispatch semantics, or lock keys.

Paths beginning with `ha_mcp_engineering/` or `config.yaml` are relative to
`hass_mcp_engineering_beta/`. Abbreviated runtime paths beginning with
`governance/`, `providers/`, or `tools/`, plus named runtime modules such as
`application.py`, are relative to
`hass_mcp_engineering_beta/ha_mcp_engineering/`. Paths beginning with `tests/`,
`docs/`, `.github/`, or another explicit repository root are repository-relative.

F4 retains multi-operation graph execution and generalized compensation.

## Dependency graph

```text
F3-0 contract freeze
        |
        v
F3-A adapter and lock core
   |          |          |
   v          v          v
 F3-B       F3-C1      F3-C2
   \          |          /
    \         |         /
     v        v        v
 F3-D recovery, observability, acceptance
        |
        v
 final F3 integration and release
```

F3-B, F3-C1, F3-C2, and F3-D may build fixtures and adapters in parallel after
F3-0 is accepted. B/C1/C2 rebase on accepted F3-A before merge. F3-D may develop
fault-injection and acceptance harnesses in parallel but merges after the
adapters it verifies.

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

## F3-B — governed dashboard writes

- Branch: `feature/f3-dashboard-writes`
- Worktree: `/home/josh/worktrees/hass-mcp-f3-dashboard-writes`
- Depends on: accepted F3-0 declarations; accepted F3-A before merge
- Merge prerequisite: exact setter evidence, dashboard risk-policy review, and
  safe bounds/compiler proof

### Ownership

Prefer isolated new files:

- `governance/dashboard_operations.py`
- `governance/dashboard_transform.py`
- `providers/upstream_dashboard_write.py`
- `tools/dashboard_write.py`
- `tests/test_f3_dashboard_transform.py`
- `tests/test_f3_dashboard_write.py`
- synthetic exact-release dashboard-write fixtures

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
- administrator approval and the complete dashboard/resource/provider lock set;
- stale-hash rejection before durable intent;
- one exact setter invocation through a deterministic generated transform;
- exact reread, no unintended field loss, and new hashes after atomic
  compare/save or all-writer exclusion has been proven;
- readback-only response-loss recovery;
- no initial rollback capability unless the separately reviewed retention gate
  is satisfied;
- negative reachability for creation, deletion, resources, preferences,
  screenshots, metadata, arbitrary Python, services, and fallback.

F3-B stops on unknown upstream behavior, unsafe transform compilation,
unbounded config/diff persistence, ambiguous selector behavior, unreviewed risk
policy, inability to prevent create-on-missing, failure to close the demonstrated
non-atomic lost-update window, or any physical/direct-service reachability.

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
- a distinctly named disposable dashboard-write apply lane for exact 7.14.2 and
  8.0.0, each proving exactly one setter invocation, one scoped fixture
  mutation, exact reread, and zero other/live/physical/fallback action; and
- full milestone harness including the governed dashboard write.

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

1. Merge F3-0 after contract review.
2. Implement and merge F3-A.
3. Rebase F3-B, F3-C1, and F3-C2 onto accepted F3-A and merge them in any
   independently green order.
4. Rebase and merge F3-D after the adapters it verifies.
5. Run an integration-only branch for central registration, health, CI,
   documentation reconciliation, versioning, and release acceptance.

Each merge re-runs complete task/schema/tool-count and exact-release invariants.
No track may use a passing neighbor branch as evidence until that neighbor is
accepted on the common base.

## Integration stop conditions

Stop the F3 merge train on runtime contract drift, schema migration without
authority, newly reachable arbitrary/write/fallback paths, inconsistent shared
file edits, changed exact-release accounting, unstable locks, possible double
dispatch, provider mutation in planning-only fixtures, any mutation outside the
single scoped dashboard in the disposable apply lane, unbounded diagnostics,
stable-v1 changes, or unresolved Critical/High review findings.
