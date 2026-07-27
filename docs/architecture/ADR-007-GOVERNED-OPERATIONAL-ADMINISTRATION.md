# ADR-007: Governed operational administration

Status: accepted for 2.1A Beta 2

## Context

Administrative operations differ from configuration writes: they can outlive
a client connection, return ambiguous transport outcomes, and may have no safe
inverse. A generic administrator or direct service-call bridge would broaden
authority beyond the reviewed Engineering contract.

## Decision

Use versioned, operation-specific governance plans with distinct proposal,
external approval, preflight, exact provider dispatch, status, independent
verification, persistent evidence, and recovery layers.

Beta 1 admitted only full-backup creation through an Engineering-owned wrapper
around exact reviewed `ha_manage_backup` contracts for 7.14.1 and 7.14.2. The
wrapper constructs `snapshot/create/name`; the upstream mixed tool is never
registered. Verification uses independent `backup/info` evidence. Dispatch
intent and approval consumption are durable before invocation. Ambiguous
outcomes can resume verification but cannot redispatch. Rollback is
unavailable.

Beta 2 extends the same contract-v3 family with exact wrappers for controlled
reload, one installed add-on restart, and Home Assistant restart. The wrappers
construct only reviewed arguments for `ha_reload_core`, `ha_manage_addon`, and
`ha_restart`; none of those mixed or high-risk tools is registered generically.
Full validation gates reload and Home Assistant restart. Independent readback,
persisted process identity, exact upstream readmission, runtime catalog,
storage, audit, and dependency evidence provide operation-specific recovery.

All operations reuse the existing upstream MCP configuration and external
Ingress approval authority. It adds no endpoint, option, credential, generic
write, fallback, restore, delete, arbitrary service, or provider argument.

## Consequences

Four proposal tools make the fully admitted catalog 45 Engineering tools plus
26 delegated reads, or 71 total. Existing contract-v1 and contract-v2
plans preserve their serialization and hashes; operational plans use contract
version 3 and a separate `operational-administration-v3` persistence namespace.
Exact 2.0.1 enumerates only the legacy namespace, so a retained-data downgrade
preserves but cannot display or operate on contract-v3 records. Re-upgrading
restores access and readback-only recovery without migration or redispatch.

The selected upstream operation excludes the recorder database and does not
independently validate archive contents. A global backup lock reduces
availability but prevents unsafe concurrent creation. Unknown or drifting
provider contracts block the operation before dispatch. The provider owns the
single precise health failure count, governance owns one lifecycle/audit event
and public mapping, and the shared transport preserves the typed validator
boundary without adding a second generic failure.

The recovery-verification model now holds distinct backup, reload, add-on, and
Home Assistant evidence. One background reconciler and startup recovery resume
only bounded readback after approved persisted dispatch. It excludes
undispatched and terminal plans, isolates each plan, shares operation-target
locks with caller reconciliation, and cannot invoke an action. Engineering
self-restart is supported because dispatch is durable before termination and
startup verifies a new process-instance ID, exact add-on identity, runtime
identity, governance health, and audit continuity without another apply call.
Successful add-on verification grades evidence as `process_identity`,
`upstream_readmission`, or the weaker `provider_acknowledgement`; historical
records without that additive field remain readable. Home Assistant restart
success requires reviewed dispatch or expected disruption evidence plus the
complete recovery contract; current connectivity alone is insufficient.
