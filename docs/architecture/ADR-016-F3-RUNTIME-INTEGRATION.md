# ADR-016: F3 runtime integration

## Status

Accepted for Beta 20 implementation and independent review.

## Context

Betas 16 through 19 shipped the F3 executor, durable lock core, canonical
contracts, and configuration and operational adapters without activating them
in public runtime routes. The legacy runtime owns one schema-1 public task per
plan and has operation-specific reconciliation. Activating F3 must preserve
that public contract while preventing legacy and F3 from both acquiring
execution authority.

Dashboard execution is excluded. The reviewed Home Assistant and `ha-mcp`
interfaces do not provide authoritative compare-and-save or exclusion of all
other dashboard writers.

## Decision

Beta 20 activates exactly eight configuration and four operational capability
identities through one closed, code-owned registry. No dynamic discovery,
caller-selected capability, fallback, dashboard capability, or new public MCP
tool is admitted.

One schema-1 public task remains the public aggregate. A durable initialization
journal atomically binds it to one deterministic `f3-child-execution-v1`
declaration per immutable plan operation. Existing legacy tasks retain legacy
authority. Existing F3 ownership remains F3 authority. Ambiguous ownership,
changed plan evidence, or corrupt storage fails before dispatch.

The execution order is fixed:

1. materialize or join the exact public-task/child authority;
2. acquire the complete durable lock set;
3. repeat final mutable preflight under those locks;
4. consume the existing approval bundle idempotently;
5. commit F3 intent and consume `dispatch_count=1`;
6. immediately invoke the one reviewed provider mutation; and
7. observe, verify, persist the child, and project the public task.

Post-intent recovery can call only adapter observation and verification. It
cannot reset intent, cancel, redispatch, or fall back to legacy execution.

One central coordinator performs a strict startup sweep before listener
readiness and bounded 30-second periodic sweeps. It is the only active F3
recovery scheduler and the scheduling authority for read-only legacy
reconciliation. Durable child claims provide cross-process single flight.

## Readiness and compatibility

Startup validates governance, audit, public-task, child, lock, ownership,
registry, and provider-admission state before enabling apply. Read-only routes
may remain diagnostic when execution readiness is unavailable, but no write
route can dispatch and no fallback is exposed.

Task schema 1, configuration plan contract 2, operational plan contract 3,
protocol `2025-03-26`, exact upstream admission, approval authority, F2 policy,
48 local tools, and stable 1.1.2 remain unchanged.

## Consequences

- Supported new forward tasks have F3 as their sole execution authority.
- Terminal historical tasks remain readable; active legacy tasks are never
  converted into dispatch-capable F3 work.
- Public projection failure cannot authorize a second dispatch.
- Dashboard infrastructure remains runtime-inert and adds no operation or tool.
- Downgrade requires resolution of nonterminal F3 work and holds; F3 records
  are never deleted automatically.
