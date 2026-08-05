# HA MCP Engineering Server 2.2.0-beta.17 release notes

## Release boundary

Beta 17 establishes one packaged `f3-operation-adapter-v1` API and retains the
runtime-inert dashboard planning and exact-verification foundation. It does not
activate F3 in current runtime routes or add executable dashboard mutation.

Independent review identified three High contract/semantic defects and one Low
schema-name defect in the initial Beta 17 head. The corrected head adds the
caller-owned idempotent approval-consumption callback after locked final
preflight and before F3 intent, binds each risk finding to complete effective
action semantics, applies strict JSON-type-aware equality to review bounds and
verification, and corrects the screenshot exclusion to `return_screenshot`.
These corrections do not activate the runtime or dashboard mutation.

The authoritative declarations now live in
`ha_mcp_engineering.f3.contracts`, which is copied into the Engineering image.
The repository-root `f3_contracts` package remains only as a compatibility and
test facade; representative and complete export checks prove its objects are
identical to the canonical classes, enums, constants, and protocol.

## Dashboard execution decision

Exact source review and deterministic interleaving prove the external-writer
lost-update race. Both reviewed `ha-mcp` 7.14.2 and 8.0.0 interfaces check a
dashboard hash separately from Home Assistant's save. The authoritative save
accepts no expected hash, receipt, transaction identity, or fencing value.

An Engineering lock cannot exclude Home Assistant UI users, integrations,
automations, or other clients. A final exact reread can verify the final state
but cannot reveal an external edit that the approved save already overwrote.
Beta 17 therefore accepts no dashboard setter realization. Generated
`python_transform` is a rejected candidate, and create-capable unrestricted
full-configuration replacement is not a workaround.

Planning retains exact storage identity, complete internal raw evidence,
bounded canonical JSON Pointer patches, deterministic proposed results, the
16-leaf semantic review limit, unknown-field preservation, bounded redacted
diffs, risk evidence, immutable private artifacts, stale-state checks, and
exact complete-readback verification. Opaque custom-card action semantics stay
manual-review evidence. Rollback remains unavailable.

## Packaging and conformance

The built-image import-closure test copies the actual shipped Python package,
starts a fresh subprocess outside the checkout, excludes repository and test
paths, and imports every shipped module. A repository-wide AST allowlist makes
new F3 dependencies explicit and keeps application startup, tools, services,
providers, governance routes, and recovery coordination disconnected.

A test-only atomicity-blocked dashboard adapter exercises the merged F3-A
executor and the exact dashboard, Home Assistant core, and provider dependency
lock set. Preflight rejects before durable intent. Dashboard dispatch is
unreachable, setter invocations are zero, and dashboard-fixture mutations are
zero. Existing F3-A synthetic success/fault paths continue to prove their
generic one-dispatch and no-blind-redispatch contract.

Approval authority remains in caller/governance code; the executor owns the
sequence. Approval consumption and F3 intent are separate durable writes, not
one claimed transaction. If loss occurs after approval but before intent, the
same F3 task/plan/operation/attempt repeats the idempotent approval callback;
provider mutation remains zero until intent is durable, and no legacy fallback
is available.

## Preserved behavior

Beta 17 adds no MCP tool, persisted `update_dashboard` operation, provider
transport, fallback, task/plan schema change, policy change, current adapter
migration, central health field, rollback, or runtime execution authority.
Dashboard v3 reads and all existing operational behavior remain unchanged.

The release preserves 25 canonical plus 23 Engineering-native tools, exact
7.14.2 78/26/74 and exact 8.0.0 78/24/72 accounting, held tools `ha_search` and
`ha_get_operation_status`, protocol `2025-03-26`, dependency pins
`aiohttp==3.14.3` and `cryptography==50.0.0`, stable v1.1.2, and zero fallback.
It does not authorize production access, deployment, publication, tagging, or
merge.
