# HA MCP Engineering Server 2.2.0-beta.19 release notes

## Release boundary

Beta 19 introduces internal F3-C2 operational adapters for the four existing
governed operations: full backup, controlled reload, exact installed add-on
restart, and Home Assistant restart. They implement
`f3-operation-adapter-v1` through the F3-A Beta 16 shared executor, durable
atomic locks, fencing, intent, deadlines, duplicate handling, cancellation,
and readback-only recovery.

The package is runtime-inert. Application startup, `ChangeGovernanceService`,
the four current planning routes, `apply_change_plan`, current restart
reconciliation, provider routing, and public MCP registration do not import or
instantiate it. F3-D remains the only activation milestone.

## Preserved operational contracts

- full backup calls exact reviewed `ha_manage_backup` snapshot/create arguments
  and retains the recorder and archive-integrity limitations;
- reload calls exact `ha_reload_core` for only automation, script,
  input-boolean, or input-number configuration;
- add-on restart calls only `ha_manage_addon(action=restart, slug=<exact>)`;
- Home Assistant restart calls only `ha_restart(confirm=true)`.

F2 policy classes, risk deltas, physical consequences, approval authority,
plan hashes/projections, baseline evidence, warnings, verification contracts,
and no-rollback declarations remain unchanged. Planning creates no execution
task, approval challenge, or provider mutation.

## Provider compatibility

The strategies wrap the existing Beta 15 gateways. Exact `ha-mcp` 7.14.2 and
8.0.0 remain bound to protocol `2025-03-26`, full 78-tool normalized catalog
admission, `ha-mcp-reviewed-normalized-catalog-v1`, and
`ha-mcp-operational-tool-descriptor-v2` with zero fallback.

7.14.2 retains its reviewed legacy lifecycle response. 8.0.0 retains
`ha-mcp-lifecycle-addon-structured-content-v1` with the
`mcp-direct-structured-content-v1` envelope, including large add-on detail.
Unknown releases, protocols, response models, envelopes, partial inventories,
and identity drift fail closed. The generic text-result bound is unchanged.

## F3 lifecycle and recovery

Each prepared operation binds exact operation/target/capability/provider,
argument hash, policy and approval evidence, baseline, verification, deadline,
and limitations. Its complete resource/provider lock union is acquired
atomically through F3-A before post-lock authoritative preflight.

F3-A durably commits dispatch lineage, request, provider operation and argument
hash, lock fencing, baseline fingerprint, immutable UTC evidence deadline,
`possibly_dispatched=true`, and `dispatch_count=1` before the provider call.
Persistence failure calls the provider zero times. Once intent exists, every
timeout, disconnect, crash, malformed response, or lost response is possibly
dispatched; process reconstruction performs observation and verification only.
The maximum adapter dispatch, provider mutation, and synthetic effect are each
one per attempt.

Operation-specific readback preserves exact backup creation evidence,
post-reload readiness, add-on identity plus restart evidence beyond old running
state, and Beta 11 bounded HA outage/reconnect/runtime/storage/admission/
dependency recovery. Ambiguous post-dispatch evidence reaches manual review
without redispatch.

## Remaining F3-D work

The F3-C2 descriptors declare exact bounded target-only conflict holds. F3-A
Beta 16 can currently promote only an entire acquired handle, so F3-D must add
or accept selective bounded hold promotion/release before activation. F3-D must
also bind the operation evidence port to existing durable task/event storage
without changing execution-task schema 1, add sibling conflict edges, and
prove migration of active restart reconciliation.

## Unchanged compatibility and authority

Beta 19 does not change:

- the 48 Engineering-local tools or public registration;
- execution-task schema version 1 or plan schemas;
- current operational, configuration, or Dashboard routes;
- exact 7.14.2 accounting of 78 advertised, 26 delegated, zero held, 48 local,
  and 74 total;
- exact 8.0.0 accounting of 78 advertised, 24 delegated, two held, 48 local,
  and 72 total;
- held tools `ha_search` and `ha_get_operation_status`;
- protocol support, policy outcomes, automatic-read accounting, or fallback;
- `aiohttp==3.14.3`, `cryptography==50.0.0`, or stable v1.1.2.

This declaration authorizes no deployment, publication, production access,
live operational action, adapter activation, merge, or release-sequence change.
F3-C2 remains ordered after accepted Beta 17 and Beta 18 delivery.
