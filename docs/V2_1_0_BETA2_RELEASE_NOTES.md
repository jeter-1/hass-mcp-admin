# HA MCP Engineering Server 2.1.0-beta.2

Status: source candidate; deployment acceptance is not part of this change.

Beta 2 completes the bounded 2.1A governed operational lifecycle. It adds three
proposal tools and reuses the existing external administrator approval and
`apply_change_plan` authority:

- `create_reload_plan`
- `create_addon_restart_plan`
- `create_home_assistant_restart_plan`

Together with Beta 1 `create_backup_plan`, these are 45 Engineering tools.
Exact admission of the existing 26 reviewed upstream reads produces 71 total
tools. The upstream 7.14.1 and 7.14.2 catalog fingerprint remains
`c6bd074d9ee1e832bd90318398c00efd9a9ffd983d5444817bc830208cbfc47c`.

## Operational contracts

Planning never dispatches an action. Every plan is immutable, expires, carries
operation and target evidence, requires exact hash-bound approval from a
distinct Home Assistant administrator principal, and persists dispatch intent
before the one permitted provider invocation.

Controlled reload accepts only `automation`, `script`, `input_boolean`, and
`input_number`. Full configuration validation and exact service discovery run
at planning and again immediately before apply. Verification requires valid
configuration, Home Assistant connectivity, service availability, and readable
domain state.

Add-on restart accepts one installed slug and binds name and installed version.
The provider can only construct `action="restart"`. Engineering self-restart
is recoverable because the dispatch record is durable before termination and a
new process-instance identity is checked at startup. The exact `ha_mcp` add-on
must regain reviewed identity, protocol, catalog, and admission.

Home Assistant restart runs full validation before the one fixed
`confirm=true` dispatch. Recovery checks Home Assistant identity, Engineering
build and 71-tool restoration, governance and audit persistence, exact upstream
admission, dependency-index recovery state, post-restart validation, and zero
fallback. Connectivity alone does not prove restart.

## Recovery and truthfulness

Provider response loss or expected restart disruption creates a durable
verification-pending state. The original MCP call need not remain open.
Background and startup reconciliation perform readback only. Repeated
`apply_change_plan` calls return the persisted result or resume verification;
they cannot repeat an action. Incomplete evidence remains pending or
indeterminate. Rollback is unavailable.

The public operational plan removes the unrelated generic configuration
verification field and identifies `operational.verification` as authoritative.
Health separates persistent plan counts, current-process activity, provider
process counters, verification-pending work, no-redispatch prevention, and
zero fallback.

## Correctness corrections

The reviewed `ha_get_entity` missing-registry `SERVICE_CALL_FAILED` outcome is
now a non-retryable `entity_not_found` result only for the exact valid
single-entity-ID lookup shape. It preserves provider/version attribution and
does not increment provider operational failures. Resolver, bulk, malformed,
and unknown tool/code/argument combinations still fail closed.

Proposal creation now records `access=proposal` and
`operation_class=proposal`; it is not mislabeled as a pure read. Approval and
apply remain writes, and the audit schema shape is unchanged.

## Exclusions

Beta 2 does not add generic service execution, arbitrary reloads, config-entry
reload, add-on start/stop/install/remove/update/configuration, backup restore or
delete, Home Assistant upgrade, entity/device mutation, dashboard writes,
2.1B risk-delta governance, 2.1C lifecycle automation, a new upstream release,
direct Home Assistant fallback, or live deployment.

## Upgrade and rollback

Upgrade in place from Beta 1 with the same slug, port, options, secrets, and
`/data`. Contract-v3 operational plans remain in
`operational-administration-v3`. Downgrading to 2.0.1 preserves that namespace
without displaying or processing it; reinstalling 2.1 restores access and
readback-only recovery. Do not recreate pending operations during a downgrade.
