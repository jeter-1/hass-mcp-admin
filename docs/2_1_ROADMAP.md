# Engineering 2.1 and 2.2 foundation roadmap

Status: 2.1A accepted; F1 corrective release `2.2.0-beta.2`

The required milestone order is:

```text
2.1A Operational Administration
→ 2.1B Broader governed administration
→ 2.1C Upstream lifecycle automation
```

The signed compatibility registry may move ahead of 2.1B only if upstream
release churn becomes the higher operational risk.

## 2.1A staged delivery

1. Beta 1 established versioned operational plans, reusable
   configuration-check evidence, externally approved full-backup creation,
   independent backup verification, indeterminate recovery, and operational
   health/audit data.
2. Beta 2 completes the remaining family coherently: controlled reload with
   planning/apply validation, exact add-on restart, Home Assistant restart,
   operation-specific verification, expected-disruption handling, and durable
   background/startup reconciliation without blind redispatch.

2.1A completed its source and separately authorized deployed acceptance.

## 2.2.0-beta.1 F1 foundation

F1 separates immutable plans, existing exact-hash approvals, and mutable
durable execution tasks. It provides task lookup, bounded listing,
pre-dispatch cancellation, exact task idempotency, append-only lifecycle
events, startup rehydration, a 24-hour post-dispatch manual-review deadline,
and legacy plan projections. Existing operation-specific verification remains
authoritative.

F1 does not implement elevated/risk-aware approval UX (F2), shared locks (F3),
compensation (F4), generalized verification (F5), comprehensive historical
terminal migration (F6), later protocol/lifecycle work (F7), or MCP-native
Tasks. Those remain future milestones and receive no implied authority from
the task record.

## 2.2.0-beta.2 F1 corrective release

Beta 2 keeps the F1 architecture and public tool surface unchanged. It
separates original provider-response receipt from later readback verification
and reconciles operation counters with durable duplicate-apply task events.
It does not begin F2 or integrate C1, E1, K1, or another development lane.

Each operation must remain operation-specific. This roadmap does not authorize
a generic administrator, arbitrary Supervisor command, service-call shortcut,
fallback, restore, or deletion.
