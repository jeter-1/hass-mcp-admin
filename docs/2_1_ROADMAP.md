# Engineering 2.1 roadmap

Status: development ordering for `2.1.0`

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

2.1A is complete only after Beta 2 source and separately authorized deployed
acceptance. 2.1B and 2.1C remain out of scope for this milestone.

Each operation must remain operation-specific. This roadmap does not authorize
a generic administrator, arbitrary Supervisor command, service-call shortcut,
fallback, restore, or deletion.
