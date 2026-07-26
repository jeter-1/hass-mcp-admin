# ADR-007: Governed operational administration

Status: accepted for 2.1A Dev1

## Context

Administrative operations differ from configuration writes: they can outlive
a client connection, return ambiguous transport outcomes, and may have no safe
inverse. A generic administrator or direct service-call bridge would broaden
authority beyond the reviewed Engineering contract.

## Decision

Use versioned, operation-specific governance plans with distinct proposal,
external approval, preflight, exact provider dispatch, status, independent
verification, persistent evidence, and recovery layers.

Dev1 admits only full-backup creation through an Engineering-owned wrapper
around exact reviewed `ha_manage_backup` contracts for 7.14.1 and 7.14.2. The
wrapper constructs `snapshot/create/name`; the upstream mixed tool is never
registered. Verification uses independent `backup/info` evidence. Dispatch
intent and approval consumption are durable before invocation. Ambiguous
outcomes can resume verification but cannot redispatch. Rollback is
unavailable.

The operation reuses the existing upstream MCP configuration and external
Ingress approval authority. It adds no endpoint, option, credential, generic
write, fallback, restore, delete, reload, or restart.

## Consequences

One new proposal tool makes the fully admitted catalog 42 Engineering tools
plus 26 delegated reads, or 68 total. Existing contract-v1 and contract-v2
plans preserve their serialization and hashes; operational plans use contract
version 3.

The selected upstream operation excludes the recorder database and does not
independently validate archive contents. A global backup lock reduces
availability but prevents unsafe concurrent creation. Unknown or drifting
provider contracts block the operation.

The recovery-verification model can later hold reload or restart recovery
evidence, but only backup fields are implemented in Dev1.
