# HA MCP Engineering Server 2.2.0-beta.16 release notes

## Release boundary

Beta 16 introduces the internal F3-A shared operation executor and durable
cross-process lock core declared by `f3-operation-adapter-v1`. These modules
are shipped in the Engineering artifact for later adapter-conformance work but
remain disconnected from current production execution.

No existing runtime adapter imports or instantiates the F3-A package. Beta 16
adds no MCP tool, startup loader, runtime registration, provider integration,
health field, dashboard-write path, fallback, or new execution authority. It
does not migrate automation, configuration, backup, controlled reload, add-on
restart, Home Assistant restart, or Dashboard v3 behavior.

## Durable lock core

The versioned lock namespace uses a stable POSIX `flock` transaction inode
across complete read, validation, conflict evaluation, generation allocation,
and atomic state replacement. It supports canonical lower-case resource and
provider keys, shared and exclusive modes, duplicate evidence union,
exclusive dominance, bytewise multi-lock acquisition, reverse release,
bounded leases and waits, renewal, exact owner/task/attempt binding, fencing
generations, and explicit stale recovery.

Multi-lock acquisition commits all requested locks or none. Corrupt records,
unknown schemas, storage failures, stale generations, and owner mismatches fail
closed. Expired locks remain conflicts until task-aware recovery explicitly
releases them, transfers the exact attempt for observation only, or creates a
manual-review conflict hold.

## Durable executor boundary

The shared executor implements planning, preflight, dispatch, observation,
verification, and recovery. Rollback remains a separate governed capability.
The caller must already have consumed approval; locks never constitute
authorization.

Before a reviewed adapter can invoke its one exact mutation, an atomic task
transaction records durable dispatch intent, the prepared operation and
target, request and attempt identity, provider operation and argument hash,
held fencing generations, timestamp, evidence deadline, and
`possibly_dispatched=true`. The same commit consumes the attempt's only
dispatch count.

Persistence failure before intent invokes the provider zero times. Once intent
exists, every crash, timeout, disconnect, malformed result, or lost response
is possibly dispatched. Reconstruction permits read-only observation and
verification only; blind redispatch is structurally prohibited.

Synthetic adapters and deterministic fault injection cover all 15 required
process-loss boundaries. The maximum durable dispatch count, adapter dispatch
count, and simulated mutation count are each one per attempt.

## Preserved behavior

Beta 16 does not change:

- public MCP tools or the 25 canonical + 23 Engineering-native = 48 local
  source contract;
- execution-task schema version 1 or plan vocabulary;
- governance, approval, routing, provider arguments, dispatch behavior, or
  fallback;
- Dashboard v3 reads, backup, reload, add-on restart, or Home Assistant
  restart;
- exact `ha-mcp` 7.14.2 accounting: 78 advertised, 26 delegated, zero held,
  48 local, and 74 total;
- exact `ha-mcp` 8.0.0 accounting: 78 advertised, 24 delegated, exactly two
  held, 48 local, and 72 total;
- held tools `ha_search` and `ha_get_operation_status`;
- protocol `2025-03-26`, dependency pins `aiohttp==3.14.3` and
  `cryptography==50.0.0`, stable v1.1.2, or zero fallback.

Production behavior remains unchanged until later F3-B, F3-C1, F3-C2, and
F3-D changes separately integrate and validate the core. This release does not
authorize deployment or live Home Assistant access.
